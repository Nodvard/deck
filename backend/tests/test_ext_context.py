"""Konkrete ExtensionContext-Handles: Permission-Pruefung zuerst, dann die
eigentliche Wirkung (docs/02-EXTENSION-API.md §2).

Alle DB-beruehrenden Handles laufen ueber `session_scope()`, das (siehe
core/vault.py) an den `db_session`-Fixture-Engine gebunden ist -- deshalb reicht hier
`db_session` als Fixture, ohne dass die Handles selbst eine Session entgegennehmen.
"""

from __future__ import annotations

import pytest
from nodvard_sdk import Actor, ActorType, DiscoveredHost, HostStatus, Notification
from nodvard_sdk.actions import REQUEST_WAIT_S, ActionRequest, ActionSpec
from nodvard_sdk.errors import PermissionDenied

from nodvard_deck.ext.context import _PermissionChecker, build_context
from nodvard_deck.ext.runtime import ExtensionRuntime, LoadedExtension
from nodvard_deck.models import Action, ExtensionRecord, Job, Notification as NotificationRow


def _manifest(ext_id: str = "test-ext", permissions: list[str] | None = None, requires: list[str] | None = None):
    from nodvard_sdk import ExtensionManifest

    return ExtensionManifest(
        id=ext_id,
        name="Test",
        version="0.1.0",
        api_version="0.1",
        entrypoint="m:E",
        permissions=permissions or [],
        requires=requires or [],
    )


def _isolated_settings(tmp_path):
    """Eigene, in `tmp_path` isolierte Settings -- niemals `config.get_settings()`
    (siehe docs/00 D-12-Nachbarschaft: Handles, die intern den globalen Singleton
    lesen statt der beim Laden uebergebenen Settings, beruehren in Tests sonst die
    echten Projektpfade)."""
    from nodvard_deck.config import Settings

    return Settings(
        data_dir=tmp_path,
        master_key_path=tmp_path / "master.key",
        vault_keyring_path=tmp_path / "vault_keyring.json",
        jwt_secret_path=tmp_path / "jwt.key",
        extensions_dir=tmp_path / "extensions",
        ext_data_dir=tmp_path / "ext-data",
    )


def _build(tmp_path, *, ext_id: str = "test-ext", permissions=None, requires=None, runtime=None, settings=None):
    runtime = runtime if runtime is not None else ExtensionRuntime()
    manifest = _manifest(ext_id, permissions, requires)
    loaded = LoadedExtension(manifest=manifest, instance=None, ctx=None, granted_permissions=manifest.permissions)
    settings = settings if settings is not None else _isolated_settings(tmp_path)
    ctx = build_context(runtime, loaded, manifest, manifest.permissions, tmp_path / "data", settings)
    loaded.ctx = ctx
    return runtime, loaded, ctx


def test_permission_checker_raises_when_missing():
    checker = _PermissionChecker("ext-1", ["hosts.read"])
    with pytest.raises(PermissionDenied):
        checker.require("hosts.write")
    checker.require("hosts.read")  # darf nicht werfen


def test_permission_checker_respects_suffix_wildcard():
    checker = _PermissionChecker("ext-1", ["secrets.read:hello-*"])
    checker.require("secrets.read:hello-world-demo")
    with pytest.raises(PermissionDenied):
        checker.require("secrets.read:other-label")


@pytest.mark.asyncio
async def test_hosts_handle_requires_permission(tmp_path):
    _, _, ctx = _build(tmp_path, permissions=[])
    with pytest.raises(PermissionDenied):
        await ctx.hosts.list()


@pytest.mark.asyncio
async def test_hosts_handle_upsert_discovered_creates_and_updates(tmp_path, db_session):
    _, _, ctx = _build(tmp_path, permissions=["hosts.read", "hosts.write"])

    created = await ctx.hosts.upsert_discovered(
        [DiscoveredHost(provider_ref="vm/100", name="docker", address="192.168.1.10")]
    )
    assert len(created) == 1
    assert created[0].name == "docker"
    # has_credential: ein
    # frisch discoverter Host hat nie ein Credential -- UND das Zugreifen darauf war
    # der eigentliche Fund (fehlendes "credentials" im D-12-Refresh unten loeste vor
    # dem Fix ein MissingGreenlet genau an dieser Stelle aus, nicht erst spaeter).
    assert created[0].has_credential is False

    updated = await ctx.hosts.upsert_discovered(
        [DiscoveredHost(provider_ref="vm/100", name="docker", address="192.168.1.99")]
    )
    assert updated[0].id == created[0].id
    assert updated[0].address == "192.168.1.99"

    listed = await ctx.hosts.list()
    assert len(listed) == 1


@pytest.mark.asyncio
async def test_hosts_handle_upsert_discovered_persists_and_resyncs_tags(tmp_path, db_session):
    """Live gefunden beim Bau der proxmox-Extension: `DiscoveredHost.tags`
    wurde weder beim Anlegen noch beim erneuten Abgleich in die DB geschrieben --
    `ctx.hosts.list(tag=...)` (die vorgesehene generische Art, "alle Hosts einer
    Extension/eines Typs" abzufragen, docs/03 §2) haette fuer JEDEN discoverten Host
    ins Leere gelaufen, unabhaengig vom Provider."""
    _, _, ctx = _build(tmp_path, permissions=["hosts.read", "hosts.write"])

    created = await ctx.hosts.upsert_discovered(
        [DiscoveredHost(provider_ref="vm/100", name="docker", address="192.168.1.10", tags=["proxmox", "vm"])]
    )
    assert set(created[0].tags) == {"proxmox", "vm"}
    assert {h.id for h in await ctx.hosts.list(tag="proxmox")} == {created[0].id}

    # Ein Tag verschwindet, ein neues kommt dazu -- Re-Sync muss beides abbilden.
    updated = await ctx.hosts.upsert_discovered(
        [DiscoveredHost(provider_ref="vm/100", name="docker", address="192.168.1.10", tags=["proxmox", "stopped"])]
    )
    assert set(updated[0].tags) == {"proxmox", "stopped"}
    assert await ctx.hosts.list(tag="vm") == []


@pytest.mark.asyncio
async def test_upsert_discovered_resync_never_removes_a_tag_it_did_not_set(tmp_path, db_session):
    """Live gefunden im Boot-Test (echter Browser): ein Admin (oder eine
    ANDERE Extension, z. B. gameserver) taggt einen bereits proxmox-entdeckten Host
    manuell mit "gameserver". Der naechste proxmox-Discovery-Zyklus (alle 5 Min)
    ueberschrieb diesen Tag bisher STILLSCHWEIGEND, weil der Tag-Abgleich den
    frisch entdeckten Satz als VOLLSTAENDIG behandelte -- exakt das Gegenteil von
    docs/02-EXTENSION-API.md Paragraph 6s Versprechen ("ein entdeckter Host mit Tag,
    nicht ein JSON-Eintrag, der unbemerkt veraltet"). Ein manuell/extern gesetzter
    Tag (`managed_by_ext_id` != der re-synchronisierenden Extension) muss einen
    erneuten Discovery-Lauf unveraendert ueberleben, auch wenn er nicht im neu
    entdeckten Tag-Satz auftaucht."""
    from nodvard_deck.db.session import session_scope
    from nodvard_deck.models import Host, HostTag

    _, _, ctx = _build(tmp_path, ext_id="proxmox", permissions=["hosts.read", "hosts.write"])

    created = await ctx.hosts.upsert_discovered(
        [DiscoveredHost(provider_ref="qemu/pve1/110", name="game-win", address="10.0.0.1", tags=["proxmox", "vm"])]
    )
    host_id = created[0].id

    # Simuliert eine andere Extension (oder einen Admin), die manuell taggt --
    # KEIN managed_by_ext_id="proxmox".
    async with session_scope() as session:
        host = await session.get(Host, host_id)
        host.tags.append(HostTag(tag="gameserver", managed_by_ext_id="gameserver"))

    # proxmoxs naechster Discovery-Zyklus kennt "gameserver" nicht -- darf ihn aber
    # NICHT entfernen, weil proxmox ihn nie gesetzt hat.
    resynced = await ctx.hosts.upsert_discovered(
        [DiscoveredHost(provider_ref="qemu/pve1/110", name="game-win", address="10.0.0.1", tags=["proxmox", "vm"])]
    )
    assert set(resynced[0].tags) == {"proxmox", "vm", "gameserver"}
    assert {h.id for h in await ctx.hosts.list(tag="gameserver")} == {host_id}


@pytest.mark.asyncio
async def test_upsert_discovered_never_overwrites_a_manually_corrected_vm_address(tmp_path, db_session):
    """Live gefunden (echte Windows-Docker-Instanz gegen einen echten
    Proxmox-Knoten): `discover_hosts()` kennt fuer VM/LXC keine echte Gast-IP und liefert
    ehrlich nur die Proxmox-API-Adresse selbst als Platzhalter (`connector.host`).
    Eine manuell auf die echte Gast-IP korrigierte Adresse (statt der
    Proxmox-Adresse als Platzhalter) darf der
    naechste 5-Minuten-Discovery-Zyklus NICHT wieder verwerfen -- sonst laeuft jeder
    SSH-Zugriff (z. B. gameservers Join-Code-Abruf) dauerhaft gegen den falschen
    Host. Fuer `kind="node"` bleibt die Adresse weiter von der Discovery gefuehrt,
    siehe `test_hosts_handle_upsert_discovered_creates_and_updates`."""
    from nodvard_deck.services import hosts as hosts_service

    _, _, ctx = _build(tmp_path, ext_id="proxmox", permissions=["hosts.read", "hosts.write"])

    created = await ctx.hosts.upsert_discovered(
        [
            DiscoveredHost(
                provider_ref="qemu/pve1/110", name="game-win", address="192.168.1.23", kind="vm"
            )
        ]
    )
    host_id = created[0].id

    await hosts_service.update_host(db_session, host_id, address="192.168.1.92")

    resynced = await ctx.hosts.upsert_discovered(
        [
            DiscoveredHost(
                provider_ref="qemu/pve1/110", name="game-win", address="192.168.1.23", kind="vm"
            )
        ]
    )
    assert resynced[0].id == host_id
    assert resynced[0].address == "192.168.1.92"


@pytest.mark.asyncio
async def test_hosts_handle_list_filters_by_group(tmp_path, db_session):
    """Live gefunden (beim Bau der scripts-Extension): `HostsHandle.list()` hat
    `group` im Protokoll angenommen, aber in der eigenen Query nie
    ausgewertet -- ein Aufruf mit `group=...` gab bisher STILL alle Hosts zurueck statt
    der gefilterten Teilmenge (kein Fehler, nur ein falsches Ergebnis). `HostGroup`
    (models/infra.py) ist ausdruecklich fuer genau diesen Fall gebaut ("Zielgruppen
    fuer Skripte"). Der Kern-Service `services.hosts.list_hosts()` filtert schon
    korrekt (siehe test_services_hosts.py) -- dieser Test haelt fest, dass die
    Extension-Sicht (`ctx.hosts.list`) dasselbe Ergebnis liefert."""
    from nodvard_deck.services import hosts as hosts_service

    _, _, ctx = _build(tmp_path, permissions=["hosts.read", "hosts.write"])

    in_group = await ctx.hosts.upsert_discovered(
        [DiscoveredHost(provider_ref="vm/1", name="in-group", address="10.0.0.1")]
    )
    outside = await ctx.hosts.upsert_discovered(
        [DiscoveredHost(provider_ref="vm/2", name="outside-group", address="10.0.0.2")]
    )

    group = await hosts_service.create_group(db_session, name="lynis-fleet")
    await hosts_service.add_group_member(db_session, group.id, in_group[0].id)

    filtered = await ctx.hosts.list(group=group.id)
    assert {h.id for h in filtered} == {in_group[0].id}
    assert outside[0].id not in {h.id for h in filtered}


@pytest.mark.asyncio
async def test_exec_handle_checks_permission_before_resolving_host(tmp_path, db_session):
    """`ExecHandle.run()` ist echt (core/ssh.py) -- dieser Test haelt nur
    noch fest, dass die Permission-Pruefung VOR jedem Host-Zugriff feuert. Der
    eigentliche SSH-Roundtrip ist in test_core_ssh.py/test_ext_terminal.py gegen
    einen echten lokalen Server getestet. `db_session` wird nicht direkt benutzt,
    aber gebraucht: es bindet `session_scope()` an die isolierte Test-Engine (siehe
    conftest.py) -- ohne sie wuerde `ExecHandle._resolve()` gegen die echten,
    ungecachten Projekt-Settings verbinden."""
    from nodvard_sdk import Host as SdkHost
    from nodvard_sdk.errors import HostUnreachable

    _, _, ctx = _build(tmp_path, permissions=[])
    with pytest.raises(PermissionDenied):
        await ctx.exec.run(host=None, command="echo hi")  # Permission-Check zuerst, `host` wird nie angefasst

    _, _, ctx2 = _build(tmp_path, permissions=["hosts.execute"])
    unknown_host = SdkHost(id="does-not-exist", name="x", display_name="x", address="1.2.3.4")
    with pytest.raises(HostUnreachable):
        await ctx2.exec.run(host=unknown_host, command="echo hi")


@pytest.mark.asyncio
async def test_exec_without_ssh_login_names_the_server_and_the_next_step(tmp_path, db_session):
    """Ein von Hand angelegter Server, der schon eine Markierung (z. B. docker) hat, aber noch keinen
    SSH-Zugang: die Fehlerkachel von Service-Matrix & Co. nennt den Server und sagt, wo es weitergeht,
    statt der internen ID."""
    from nodvard_sdk.errors import HostUnreachable

    from nodvard_deck.services import hosts as hosts_service
    from nodvard_deck.services.hosts import host_to_sdk

    host = await hosts_service.create_host(db_session, name="nas", display_name="Mein NAS", address="192.168.2.20")
    await db_session.commit()
    _, _, ctx = _build(tmp_path, permissions=["hosts.execute"])
    with pytest.raises(HostUnreachable) as raised:
        await ctx.exec.run(host=host_to_sdk(host), command="true")
    text = str(raised.value)
    assert "Mein NAS" in text and "SSH-Zugang" in text and "Server & Zugänge" in text
    assert host.id not in text


@pytest.mark.asyncio
async def test_exec_handle_run_blocks_deny_pattern_before_touching_the_host(tmp_path, db_session):
    """Verteidigung in der Tiefe: `ctx.exec.run()` ist technisch nicht auf
    lesende Kommandos beschraenkt (Extensions sind vertrauenswuerdiger Code, keine
    Sandbox, docs/01 §7) -- ein Sperrmuster wird trotzdem VOR jedem Host-Zugriff
    abgefangen. `host=None` beweist das: ein `HostUnreachable`/`AttributeError` beim
    Aufloesen wuerde diesen Test ebenfalls durchfallen lassen, wenn die Reihenfolge
    falsch waere."""
    from nodvard_sdk.errors import ActionBlocked
    from sqlalchemy import select

    from nodvard_deck.models import AuditEntry

    _, _, ctx = _build(tmp_path, permissions=["hosts.execute"])
    with pytest.raises(ActionBlocked):
        await ctx.exec.run(host=None, command="rm -rf /")

    rows = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "exec.denied"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].outcome == "denied"
    assert rows[0].actor_id == "test-ext"


@pytest.mark.asyncio
async def test_actions_result_only_visible_to_the_proposing_extension(tmp_path, db_session):
    _, _, ctx = _build(tmp_path, permissions=["hosts.execute"])
    decision = await ctx.actions.propose(
        ActionRequest(
            action_type="shell.exec",
            proposed_by=Actor(type=ActorType.EXTENSION, id="test-ext"),
            reason="Testvorschlag",
        )
    )

    own = await ctx.actions.result(decision.action_id)
    assert own is not None
    assert own.id == decision.action_id

    _, _, other_ctx = _build(tmp_path, ext_id="other-ext", permissions=["hosts.execute"])
    assert await other_ctx.actions.result(decision.action_id) is None
    assert await ctx.actions.result("does-not-exist") is None


@pytest.mark.asyncio
async def test_actions_proposer_labels_name_the_proposer_of_own_rows_only(tmp_path, db_session):
    """`ctx.actions.proposer_labels()` macht aus user/<uuid> den Nutzernamen --
    aber nur fuer Zeilen der eigenen Extension (kein Umsetzer fuer fremde Nutzer-IDs)."""
    from nodvard_deck.core import security
    from nodvard_deck.models import User

    user = User(username="kollegin", password_hash=security.hash_password("whatever123"), is_active=True)
    db_session.add(user)
    db_session.add(ExtensionRecord(
        id="test-ext", version="0.1.0", api_version="0.1", state="enabled", manifest={"name": "Test-Erweiterung"},
    ))
    await db_session.flush()

    _, _, ctx = _build(tmp_path, permissions=["hosts.execute"])
    _, _, other_ctx = _build(tmp_path, ext_id="other-ext", permissions=["hosts.execute"])
    for who in (Actor.user(user.id, "kollegin"), Actor(type=ActorType.EXTENSION, id="test-ext"), Actor.ai("qwen")):
        await ctx.actions.propose(ActionRequest(action_type="shell.exec", proposed_by=who, reason="Test"))
    await other_ctx.actions.propose(
        ActionRequest(action_type="shell.exec", proposed_by=Actor.user(user.id, "kollegin"), reason="Fremd")
    )

    own = await ctx.actions.list()
    foreign = await other_ctx.actions.list()
    labels = await ctx.actions.proposer_labels([*own, *foreign])

    by_reason_type = {row.proposed_by_type: labels[row.id] for row in own}
    assert by_reason_type == {"user": "kollegin", "extension": "Test-Erweiterung", "ai": "KI"}
    assert labels[foreign[0].id] == f"user/{user.id}", "fremde Zeilen bleiben roh"
    assert await ctx.actions.proposer_labels([]) == {}


@pytest.mark.asyncio
async def test_actions_proposer_labels_ignore_hand_made_rows(tmp_path, db_session):
    """Aufgeloest wird nach der Datenbank, nicht nach den uebergebenen Zeilen --
    eine selbstgebaute Action mit fremder Nutzer-ID (oder mit der ID einer fremden echten
    Zeile) verrät keinen Namen, auch nicht mit `ext_id` der eigenen Extension."""
    from nodvard_deck.core import security
    from nodvard_deck.models import Action, User

    user = User(username="geheimer-admin", password_hash=security.hash_password("whatever123"), is_active=True)
    db_session.add(user)
    await db_session.flush()

    _, _, ctx = _build(tmp_path, permissions=[])
    _, _, other_ctx = _build(tmp_path, ext_id="other-ext", permissions=["hosts.execute"])
    await other_ctx.actions.propose(
        ActionRequest(action_type="shell.exec", proposed_by=Actor.user(user.id, "geheimer-admin"), reason="Fremd")
    )
    foreign = (await other_ctx.actions.list())[0]

    fake = Action(
        id="selbstgebaut", ext_id="test-ext", action_type="shell.exec", payload={}, risk="low", status="proposed",
        proposed_by_type="user", proposed_by_id=user.id, reason="", gate_decision={},
    )
    # Die Zeile einer fremden Extension mit dem eigenen ext_id umetikettiert (nur im Speicher):
    relabelled = Action(
        id=foreign.id, ext_id="test-ext", action_type="shell.exec", payload={}, risk="low", status="proposed",
        proposed_by_type="user", proposed_by_id=user.id, reason="", gate_decision={},
    )

    labels = await ctx.actions.proposer_labels([fake, relabelled])
    assert labels == {"selbstgebaut": f"user/{user.id}", foreign.id: f"user/{user.id}"}
    assert "geheimer-admin" not in str(labels)


@pytest.mark.asyncio
async def test_secrets_and_vault_use_roundtrip_through_context(tmp_path, db_session):
    _, _, ctx = _build(tmp_path, permissions=["secrets.read:demo-*"])

    handle = await ctx.secrets.create(label="demo-secret", kind="generic", value="geheim")
    assert await ctx.secrets.exists("demo-secret")

    async with ctx.vault_use(handle) as value:
        assert value == "geheim"


@pytest.mark.asyncio
async def test_settings_handle_get_set_roundtrip(tmp_path, db_session):
    db_session.add(ExtensionRecord(id="test-ext", version="0.1.0", api_version="0.1", state="enabled"))
    await db_session.commit()

    _, _, ctx = _build(tmp_path, permissions=[])
    ctx.settings.declare({"type": "object"})
    assert await ctx.settings.get() == {}

    await ctx.settings.set({"greeting": "hallo"})
    assert await ctx.settings.get() == {"greeting": "hallo"}


@pytest.mark.asyncio
async def test_settings_set_calls_on_settings_changed_after_commit(tmp_path, db_session):
    """Das SDK versprach den Hook, der Kern rief ihn nie auf. Jetzt: nach dem
    Speichern, mit den neuen Werten lesbar, ohne Endlosschleife bei set() im Hook, und
    ein Fehler im Hook macht das gespeicherte set() nicht rueckgaengig."""
    db_session.add(ExtensionRecord(id="test-ext", version="0.1.0", api_version="0.1", state="enabled"))
    await db_session.commit()
    _, loaded, ctx = _build(tmp_path, permissions=[])
    seen: list[tuple[dict, dict]] = []

    class _Ext:
        async def on_settings_changed(self, hook_ctx, values):
            seen.append((values, await hook_ctx.settings.get()))
            await hook_ctx.settings.set({**values, "normalized": True})  # darf NICHT erneut ausloesen

    loaded.instance = _Ext()
    await ctx.settings.set({"docker_host_tag": "container"})
    assert seen == [({"docker_host_tag": "container"}, {"docker_host_tag": "container"})]
    assert await ctx.settings.get() == {"docker_host_tag": "container", "normalized": True}

    class _Broken:
        async def on_settings_changed(self, hook_ctx, values):
            raise RuntimeError("kaputt")

    loaded.instance = _Broken()
    await ctx.settings.set({"docker_host_tag": "x"})
    assert await ctx.settings.get() == {"docker_host_tag": "x"}


def test_register_host_tool_replaces_same_id():
    from nodvard_sdk import HostToolSpec

    runtime = ExtensionRuntime()
    runtime.ui.register_host_tool("ext-a", HostToolSpec(id="c", title="Container", path="/m", tags=["docker"]))
    runtime.ui.register_host_tool("ext-a", HostToolSpec(id="c", title="Container", path="/m", tags=["podman"]))
    runtime.ui.register_host_tool("ext-a", HostToolSpec(id="other", title="Anderes", path="/o"))
    tools = runtime.ui.all_host_tools()
    assert [(e, t.id, t.tags) for e, t in tools] == [("ext-a", "c", ["podman"]), ("ext-a", "other", [])]
    runtime.ui.clear_extension("ext-a")
    assert runtime.ui.all_host_tools() == []


def test_register_host_requirement_replaces_same_id_and_clears_with_the_extension():
    from nodvard_sdk import HostRequirementSpec

    runtime = ExtensionRuntime()
    runtime.ui.register_host_requirement("ext-a", HostRequirementSpec(id="g", label="Docker", unix_group="docker", tags=["docker"]))
    runtime.ui.register_host_requirement("ext-a", HostRequirementSpec(id="g", label="Docker", unix_group="docker", tags=["podman"]))
    runtime.ui.register_host_requirement("ext-a", HostRequirementSpec(id="root", label="Root", needs_root=True))
    runtime.ui.register_host_requirement("ext-b", HostRequirementSpec(id="g", label="Anderes"))
    assert [(e, r.id, r.tags) for e, r in runtime.ui.all_host_requirements()] == [
        ("ext-a", "g", ["podman"]), ("ext-a", "root", []), ("ext-b", "g", []),
    ]
    # Deaktivieren raeumt nur die Meldungen DIESER Extension weg.
    runtime.ui.clear_extension("ext-a")
    assert [(e, r.label) for e, r in runtime.ui.all_host_requirements()] == [("ext-b", "Anderes")]
    runtime.ui.clear_extension("ext-b")
    assert runtime.ui.all_host_requirements() == []


def test_ui_handle_registers_host_requirement_under_its_own_extension(tmp_path):
    from nodvard_sdk import HostRequirementSpec

    runtime, _, ctx = _build(tmp_path)
    ctx.ui.register_host_requirement(HostRequirementSpec(id="root", label="Root", needs_root=True))
    assert [(e, r.id) for e, r in runtime.ui.all_host_requirements()] == [("test-ext", "root")]


@pytest.mark.asyncio
async def test_notify_handle_requires_permission_and_writes_row(tmp_path, db_session):
    _, _, ctx = _build(tmp_path, permissions=["notify.send"])
    await ctx.notify.send(Notification(title="Hallo", body="Welt"))

    from sqlalchemy import select

    rows = (await db_session.execute(select(NotificationRow))).scalars().all()
    assert len(rows) == 1
    assert rows[0].source_ext_id == "test-ext"


@pytest.mark.asyncio
async def test_scheduler_register_job_requires_permission_and_writes_row(tmp_path, db_session):
    async def _handler(**kwargs):
        return None

    class _Spec:
        id = "job-1-key"
        name = "job-1"
        schedule = "* * * * *"
        enabled = True
        params: dict = {}
        handler = staticmethod(_handler)

    _, _, ctx = _build(tmp_path, permissions=[])
    with pytest.raises(PermissionDenied):
        await ctx.scheduler.register_job(_Spec())

    _, _, ctx2 = _build(tmp_path, permissions=["schedule.register"])
    await ctx2.scheduler.register_job(_Spec())

    from sqlalchemy import select

    rows = (await db_session.execute(select(Job))).scalars().all()
    assert len(rows) == 1
    assert rows[0].name == "job-1"
    assert rows[0].ext_job_key == "job-1-key"

    # Erneutes register_job() (jedes Extension-Enable) dupliziert NICHT --
    # genau der frueher moegliche doppelte-Cron-Fehler (docs/00 D-08).
    await ctx2.scheduler.register_job(_Spec())
    rows_after_second_register = (await db_session.execute(select(Job))).scalars().all()
    assert len(rows_after_second_register) == 1


@pytest.mark.asyncio
async def test_actions_propose_creates_row_with_proposed_status(tmp_path, db_session):
    _, _, ctx = _build(tmp_path, permissions=["hosts.execute"])
    decision = await ctx.actions.propose(
        ActionRequest(
            action_type="shell.exec",
            proposed_by=Actor(type=ActorType.EXTENSION, id="test-ext"),
            reason="Testvorschlag",
        )
    )
    assert decision.status.value == "proposed"

    from sqlalchemy import select

    rows = (await db_session.execute(select(Action))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "proposed"
    assert rows[0].reason == "Testvorschlag"


@pytest.mark.asyncio
async def test_actions_propose_checks_registered_spec_permissions(tmp_path, db_session):
    _, _, ctx = _build(tmp_path, permissions=[])
    ctx.actions.register(
        ActionSpec(action_type="custom.thing", label="Custom", permissions=["hosts.execute"])
    )
    with pytest.raises(PermissionDenied):
        await ctx.actions.propose(
            ActionRequest(
                action_type="custom.thing",
                proposed_by=Actor(type=ActorType.EXTENSION, id="test-ext"),
                reason="x",
            )
        )


@pytest.mark.asyncio
async def test_actions_propose_accepts_specs_that_are_not_host_bound(tmp_path, db_session):
    """host_bound=False sperrt nur die Server-Seite (POST /hosts/{id}/actions);
    die Extension selbst schlaegt solche Aktionen weiter vor -- samt Sperrliste ueber
    `command_field`."""
    _, _, ctx = _build(tmp_path, permissions=["hosts.execute"])
    ctx.actions.register(
        ActionSpec(action_type="custom.intern", label="Intern", host_bound=False,
                   permissions=["hosts.execute"], command_field="command")
    )
    proposed = await ctx.actions.propose(
        ActionRequest(action_type="custom.intern", payload={"command": "uptime"},
                      proposed_by=Actor(type=ActorType.EXTENSION, id="test-ext"), reason="x")
    )
    assert proposed.status.value == "proposed"
    denied = await ctx.actions.propose(
        ActionRequest(action_type="custom.intern", payload={"command": "rm -rf /"},
                      proposed_by=Actor(type=ActorType.EXTENSION, id="test-ext"), reason="x")
    )
    assert denied.status.value == "denied"



@pytest.mark.asyncio
async def test_actions_propose_passes_wait_s_to_the_gate(tmp_path, db_session, monkeypatch):
    """Extension-Routen begrenzen mit `wait_s` das Warten im Modus 'full';
    ohne Angabe wartet `propose()` wie bisher bis zum Ende (None)."""
    from nodvard_deck.core import gate as gate_service

    seen: list[float | None] = []
    real_propose = gate_service.propose

    async def _spy(session, **kwargs):
        seen.append(kwargs.get("wait_s"))
        return await real_propose(session, **kwargs)

    monkeypatch.setattr(gate_service, "propose", _spy)
    _, _, ctx = _build(tmp_path, permissions=["hosts.execute"])
    request = ActionRequest(
        action_type="shell.exec", proposed_by=Actor(type=ActorType.EXTENSION, id="test-ext"), reason="x",
    )
    await ctx.actions.propose(request)
    await ctx.actions.propose(request, wait_s=REQUEST_WAIT_S)
    assert seen == [None, REQUEST_WAIT_S]
    assert gate_service.WAIT_S == REQUEST_WAIT_S

def test_capabilities_provide_rejects_unknown_protocol(tmp_path):
    _, _, ctx = _build(tmp_path)
    with pytest.raises(Exception):
        ctx.capabilities.provide(object())


@pytest.mark.asyncio
async def test_capabilities_query_restricted_to_requires(tmp_path):
    """docs/02 §2: andere Extensions sind nur ueber deklarierte `requires` sichtbar."""
    from nodvard_sdk.capabilities import SearchProvider

    class _Search:
        async def search(self, query: str, *, limit: int = 20):
            return []

    runtime = ExtensionRuntime()
    _, _, provider_ctx = _build(tmp_path, ext_id="provider-ext", runtime=runtime)
    provider_ctx.capabilities.provide(_Search())

    _, _, consumer_without_requires = _build(tmp_path, ext_id="consumer-a", requires=[], runtime=runtime)
    assert await consumer_without_requires.capabilities.query(SearchProvider) == []

    _, _, consumer_with_requires = _build(
        tmp_path, ext_id="consumer-b", requires=["provider-ext"], runtime=runtime
    )
    found = await consumer_with_requires.capabilities.query(SearchProvider)
    assert len(found) == 1
    assert isinstance(found[0], _Search)


def test_http_handle_blocks_ip_outside_granted_cidr(tmp_path):
    _, _, ctx = _build(tmp_path, permissions=["net.outbound:192.168.1.0/24"])
    with pytest.raises(PermissionDenied):
        ctx.http._require_target_allowed("http://10.0.0.5/status")
    ctx.http._require_target_allowed("http://192.168.1.42/status")  # darf nicht werfen


def test_http_handle_requires_any_net_outbound_permission():
    from nodvard_deck.ext.context import HttpHandle

    checker = _PermissionChecker("ext-1", [])
    handle = HttpHandle(checker, [])
    with pytest.raises(PermissionDenied):
        handle._require_target_allowed("http://example.com/")


def test_http_handle_stream_checks_permission_before_connecting():
    """`ctx.http.stream()` ist neu (fehlte bisher komplett -- `AIProvider.stream()`
    musste deshalb das VOLLSTAENDIGE Ergebnis als einen Chunk
    liefern). Wie jede andere `HttpHandle`-Methode muss
    die Permission-Pruefung VOR jedem Verbindungsversuch feuern."""
    from nodvard_deck.ext.context import HttpHandle

    checker = _PermissionChecker("ext-1", [])
    handle = HttpHandle(checker, [])
    with pytest.raises(PermissionDenied):
        handle.stream("GET", "http://example.com/")


def test_http_handle_insecure_tls_requires_dedicated_permission():
    """`net.outbound` allein reicht NICHT fuer
    `insecure_tls=True` -- das waere sonst ein stiller Freifahrtschein, TLS-Pruefung
    fuer jedes Ziel abzuschalten, das `net.outbound` ohnehin schon erlaubt."""
    from nodvard_deck.ext.context import HttpHandle

    handle = HttpHandle(_PermissionChecker("ext-1", ["net.outbound"]), ["net.outbound"])
    with pytest.raises(PermissionDenied):
        handle._client_for(insecure_tls=True)


def test_http_handle_insecure_tls_uses_a_separate_cached_client():
    """Mit der eigenen Berechtigung: ein ZWEITER, von `_client` unabhaengiger Client
    (sonst wuerde `verify=False` auch fuer sichere Aufrufe gelten -- der Normalfall
    ohne `insecure_tls` darf davon nicht beruehrt sein), aber lazy und wiederverwendet
    (kein neuer Client pro Aufruf)."""
    from nodvard_deck.ext.context import HttpHandle

    handle = HttpHandle(
        _PermissionChecker("ext-1", ["net.outbound", "net.outbound.insecure_tls"]),
        ["net.outbound", "net.outbound.insecure_tls"],
    )
    secure = handle._client_for(insecure_tls=False)
    insecure_first = handle._client_for(insecure_tls=True)
    insecure_second = handle._client_for(insecure_tls=True)

    assert secure is handle._client
    assert insecure_first is not secure
    assert insecure_first is insecure_second  # gecacht, nicht bei jedem Aufruf neu gebaut


@pytest.mark.asyncio
async def test_http_handle_get_with_insecure_tls_dispatches_through_the_insecure_client():
    """End-zu-Ende innerhalb von `get()` selbst (nicht nur `_client_for()` direkt) --
    beweist, dass `insecure_tls=True` tatsaechlich ankommt, statt nur theoretisch
    verdrahtet zu sein."""
    import httpx
    from nodvard_deck.ext.context import HttpHandle

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    handle = HttpHandle(
        _PermissionChecker("ext-1", ["net.outbound", "net.outbound.insecure_tls"]),
        ["net.outbound", "net.outbound.insecure_tls"],
    )
    fake_transport = httpx.ASGITransport(app=app)
    handle._insecure_client = httpx.AsyncClient(transport=fake_transport, base_url="http://test")

    response = await handle.get("http://test/status", insecure_tls=True)
    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.asyncio
async def test_http_handle_stream_yields_an_async_iterable_response():
    """`stream()` muss die `httpx.AsyncClient.stream()`-Vertragsform (`async with ...
    as response: async for chunk in response.aiter_bytes()`) unveraendert
    durchreichen -- NICHT `request()`/`get()` aufrufen, die den gesamten Body vor der
    Rueckgabe lesen. `httpx.ASGITransport` fasst den Body serverseitig zu einer
    Antwort zusammen (kein echter Socket, kein TCP-Chunking beobachtbar) -- dieser
    Test beweist deshalb die korrekte API-Form und den korrekten Inhalt, nicht die
    Chunk-Granularitaet ueber ein echtes Netzwerk (das uebernimmt httpx selbst)."""
    import httpx
    from nodvard_deck.ext.context import HttpHandle

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        for chunk in (b"erster-", b"zweiter-", b"dritter"):
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    handle = HttpHandle(_PermissionChecker("ext-1", ["net.outbound"]), ["net.outbound"])
    handle._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    received: list[bytes] = []
    async with handle.stream("GET", "http://test/stream") as response:
        assert response.status_code == 200
        async for chunk in response.aiter_bytes():
            if chunk:
                received.append(chunk)

    assert b"".join(received) == b"erster-zweiter-dritter"


# inventory-Extension (docs/02-EXTENSION-API.md §7 "eigener
# Alembic-Branch"): `ext.tables.validate_table_prefix()` existierte lange nur
# synthetisch getestet, nie von echtem Code aufgerufen (`ext/tables.py`s eigener
# Docstring: "ohne eine Extension mit eigenem Schema waere das spekulativ gegen
# nichts Echtes"). `DbHandle.declare_tables()` ist der erste echte Aufrufer.


def test_db_handle_declare_tables_accepts_a_correctly_prefixed_metadata():
    from sqlalchemy import Column, Integer, MetaData, String, Table

    from nodvard_deck.ext.context import DbHandle

    metadata = MetaData()
    Table("ext_inventory_items", metadata, Column("id", String(36), primary_key=True), Column("qty", Integer()))

    handle = DbHandle("inventory", "ext_inventory_")
    handle.declare_tables(metadata)  # wirft nicht


def test_db_handle_declare_tables_rejects_a_wrongly_prefixed_table():
    from sqlalchemy import Column, MetaData, String, Table

    from nodvard_deck.ext.context import DbHandle
    from nodvard_deck.ext.tables import TablePrefixViolation

    metadata = MetaData()
    Table("items", metadata, Column("id", String(36), primary_key=True))  # kein "ext_inventory_"-Praefix

    handle = DbHandle("inventory", "ext_inventory_")
    with pytest.raises(TablePrefixViolation, match="items"):
        handle.declare_tables(metadata)


def test_http_handle_websocket_checks_target_permission_before_connecting():
    """Konsole: `ctx.http.websocket()` ist ein neuer Ausgang nach draussen --
    dieselbe Zielpruefung wie `get()`/`stream()`, und zwar beim AUFRUF, bevor
    irgendeine Verbindung entsteht."""
    from nodvard_deck.ext.context import HttpHandle

    handle = HttpHandle(_PermissionChecker("ext-1", []), [])
    with pytest.raises(PermissionDenied):
        handle.websocket("ws://example.com/socket")

    limited = HttpHandle(
        _PermissionChecker("ext-1", ["net.outbound:192.168.1.0/24"]), ["net.outbound:192.168.1.0/24"]
    )
    with pytest.raises(PermissionDenied):
        limited.websocket("wss://10.0.0.5:8006/socket")


def test_http_handle_websocket_insecure_tls_requires_dedicated_permission():
    from nodvard_deck.ext.context import HttpHandle

    handle = HttpHandle(_PermissionChecker("ext-1", ["net.outbound"]), ["net.outbound"])
    with pytest.raises(PermissionDenied):
        handle.websocket("wss://192.168.1.23:8006/socket", insecure_tls=True)


@pytest.mark.asyncio
async def test_http_handle_websocket_connects_with_headers_and_subprotocol():
    """Echte Verbindung gegen einen echten lokalen WebSocket-Server: Header und
    Subprotokoll kommen an, Binaerdaten gehen hin und zurueck."""
    import websockets
    from nodvard_deck.ext.context import HttpHandle

    seen: dict = {}

    async def handler(conn):  # noqa: ANN001, ANN202
        seen["auth"] = conn.request.headers.get("Authorization")
        seen["subprotocol"] = conn.subprotocol
        data = await conn.recv()
        await conn.send(b"echo:" + data)

    async with websockets.serve(handler, "127.0.0.1", 0, subprotocols=["binary"]) as server:
        port = server.sockets[0].getsockname()[1]
        handle = HttpHandle(_PermissionChecker("ext-1", ["net.outbound"]), ["net.outbound"])
        async with handle.websocket(
            f"ws://127.0.0.1:{port}/x", headers={"Authorization": "Token abc"}, subprotocols=["binary"]
        ) as ws:
            await ws.send(b"\x00\x01")
            assert await ws.recv() == b"echo:\x00\x01"

    assert seen == {"auth": "Token abc", "subprotocol": "binary"}


@pytest.mark.asyncio
async def test_exec_handle_stream_requires_permission_at_call_time(tmp_path):
    """Container-Verwaltung: `ctx.exec.stream()` (Live-Logs) ist ein zweiter
    Weg zu einem entfernten Kommando -- dieselbe Berechtigung wie `run()`, geprueft
    schon beim Aufruf."""
    _, _, ctx = _build(tmp_path, permissions=[])
    with pytest.raises(PermissionDenied):
        ctx.exec.stream(None, "docker logs web")


@pytest.mark.asyncio
async def test_exec_handle_stream_blocks_deny_pattern_before_touching_the_host(tmp_path, db_session):
    """Wie `run()`: ein Sperrmuster endet VOR jedem Host-Zugriff (`host=None` wuerde
    sonst beim Aufloesen scheitern) und hinterlaesst eine Audit-Zeile."""
    from nodvard_sdk.errors import ActionBlocked
    from sqlalchemy import select

    from nodvard_deck.models import AuditEntry

    _, _, ctx = _build(tmp_path, permissions=["hosts.execute"])
    with pytest.raises(ActionBlocked):
        async with ctx.exec.stream(None, "rm -rf /"):
            pass

    rows = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "exec.denied"))).scalars().all()
    assert [r.outcome for r in rows] == ["denied"]


@pytest.mark.asyncio
async def test_upsert_discovered_address_change_drops_pooled_ssh_connections(tmp_path, db_session, monkeypatch):
    """Ein Adresswechsel per Abgleich muss eine gepoolte Verbindung verwerfen, sonst
    arbeitet sie weiter an der alten Adresse."""
    from nodvard_deck.core import ssh

    dropped: list[str] = []

    class _Pool:
        async def drop_host(self, host_id: str) -> None:
            dropped.append(host_id)

    monkeypatch.setattr(ssh, "_pool", _Pool())
    _, _, ctx = _build(tmp_path, permissions=["hosts.read", "hosts.write"])
    created = await ctx.hosts.upsert_discovered(
        [DiscoveredHost(provider_ref="vm/100", name="docker", address="192.168.1.10")]
    )
    await ctx.hosts.upsert_discovered(
        [DiscoveredHost(provider_ref="vm/100", name="docker", address="192.168.1.10")]
    )
    assert dropped == []
    await ctx.hosts.upsert_discovered(
        [DiscoveredHost(provider_ref="vm/100", name="docker", address="192.168.1.11")]
    )
    assert set(dropped) == {created[0].id}  # (nach dem Commit kommt derselbe Drop nochmals)
