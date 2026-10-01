"""`core/demo_seed.py` -- Beispieldaten und `NODVARD_DECK_DEMO_MODE` (docs/00-DECISIONS.md D-10,
"Demo-Image zum Selbst-Antesten"). Die Seed-Funktion wird direkt gegen `db_session` getestet; der
Endpunkt-Weg (Rechte, Audit, Layout) steht in `test_demo_api.py`. Am Ende der Datei prueft ein Test den
echten Start (`main.lifespan`) mit gesetztem Demo-Modus.
"""

from __future__ import annotations

import ipaddress

import pytest
from nodvard_deck.core import demo_seed
from nodvard_deck.core.demo_seed import DemoBlockedError, remove_demo_data, seed_demo_data
from nodvard_deck.core.demo_seed import demo_status as demo_status_of
from nodvard_deck.models import AuditEntry, CustomApp, Host, Notification
from nodvard_deck.services import custom_apps as custom_apps_service
from nodvard_deck.services import hosts as hosts_service
from sqlalchemy import select


@pytest.mark.asyncio
async def test_seeds_generic_demo_hosts_into_an_empty_database(db_session):
    status, created = await seed_demo_data(db_session)
    assert created is True
    assert status.active and status.hosts == 5

    rows = (await db_session.execute(select(Host))).scalars().all()
    names = {h.name for h in rows}
    assert names == {"demo-nas", "demo-webserver", "demo-pi", "demo-sicherung", "demo-testrechner"}
    assert {h.status for h in rows} == {"up", "down", "unknown"}
    for h in rows:
        # Dokumentationsbereich (RFC 5737): nie ein echtes Netz.
        assert ipaddress.ip_address(h.address) in ipaddress.ip_network("192.0.2.0/24")
        assert h.credentials == []
        # Kern-Purity: keine Vendor-Namen in den Demo-Hosts (das waere ein
        # check_core_purity.py-Verstoss, wenn diese Datei je unter backend/src/nodvard_deck
        # gelesen wuerde -- hier zusaetzlich als Verhaltensgarantie, nicht nur als Wortscan).
        assert "proxmox" not in h.name.lower()
        assert "docker" not in h.name.lower()
        assert "docker" not in {t.tag for t in h.tags}


@pytest.mark.asyncio
async def test_does_not_seed_when_a_real_host_already_exists(db_session):
    await hosts_service.create_host(db_session, name="real-host", address="10.0.0.5")
    await db_session.flush()

    with pytest.raises(DemoBlockedError):
        await seed_demo_data(db_session)

    rows = (await db_session.execute(select(Host))).scalars().all()
    assert [h.name for h in rows] == ["real-host"]  # keine Demo-Hosts hinzugekommen
    assert (await db_session.execute(select(Notification))).scalars().all() == []


@pytest.mark.asyncio
async def test_seeding_twice_does_not_duplicate(db_session):
    _, first = await seed_demo_data(db_session)
    assert first is True

    status, second = await seed_demo_data(db_session)
    assert second is False  # bereits befuellt, kein zweiter Seed-Lauf
    assert status.hosts == 5

    assert len((await db_session.execute(select(Host))).scalars().all()) == 5
    assert len((await db_session.execute(select(Notification))).scalars().all()) == status.notifications


@pytest.mark.asyncio
async def test_demo_data_marks_only_its_own_rows(db_session):
    """Die Markierung ist die ID-Liste in der Einstellung -- nichts am Host selbst verraet ihn."""
    await seed_demo_data(db_session)
    record = await demo_seed._record(db_session)
    hosts = (await db_session.execute(select(Host))).scalars().all()
    assert sorted(record["host_ids"]) == sorted(h.id for h in hosts)
    assert len(record["notification_ids"]) == 4
    apps = (await db_session.execute(select(CustomApp))).scalars().all()
    assert sorted(record["app_ids"]) == sorted(a.id for a in apps)

    removal = await remove_demo_data(db_session)
    assert removal.removed_hosts == 5 and removal.removed_notifications == 4 and removal.removed_apps == 3
    assert (await demo_seed.demo_status(db_session)).active is False


@pytest.mark.asyncio
async def test_system_actor_is_audited_without_a_user(db_session):
    await seed_demo_data(db_session)
    await remove_demo_data(db_session)
    rows = (await db_session.execute(select(AuditEntry).order_by(AuditEntry.ts, AuditEntry.id))).scalars().all()
    assert [r.action for r in rows] == ["system.demo.seeded", "system.demo.removed"]
    assert all(r.actor_type == "system" for r in rows)


@pytest.mark.asyncio
async def test_demo_hosts_are_not_touched_by_background_collection(db_session):
    """Ohne Zugang: weder der Metrik-Sammler noch die Statuspruefung baut je eine Verbindung auf."""
    from nodvard_deck.core.metrics_history import resolve_metrics_provider
    from nodvard_deck.services.hosts import host_to_sdk

    await seed_demo_data(db_session)
    for host in (await db_session.execute(select(Host))).scalars().all():
        sdk_host = host_to_sdk(host)
        assert sdk_host.has_credential is False
        assert host.enabled is True
        assert await resolve_metrics_provider(sdk_host, host.provider_ext_id) is None


@pytest.mark.asyncio
async def test_demo_mode_seeds_on_startup_and_second_start_changes_nothing(tmp_path, monkeypatch):
    """Der echte Start (main.py-Lifespan) mit `NODVARD_DECK_DEMO_MODE=1` nutzt dieselbe Funktion."""
    from nodvard_deck import config
    from nodvard_deck.core.events import reset_event_bus
    from nodvard_deck.core.metrics_history import reset_metrics_collector
    from nodvard_deck.core.scheduler import reset_scheduler_service
    from nodvard_deck.db.session import (
        create_engine_for,
        reset_engine_cache,
        set_engine_for_testing,
    )
    from nodvard_deck.main import app, lifespan
    from nodvard_deck.models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker

    settings = config.Settings(
        env="dev", data_dir=tmp_path, database_url=f"sqlite+aiosqlite:///{tmp_path / 'demo.db'}",
        master_key_path=tmp_path / "master.key", vault_keyring_path=tmp_path / "vault_keyring.json",
        jwt_secret_path=tmp_path / "jwt_secret.key", extensions_dir=tmp_path / "extensions",
        ext_data_dir=tmp_path / "ext-data", metrics_interval_s=0, demo_mode=True,
    )
    monkeypatch.setattr(config, "_settings", settings)
    engine = create_engine_for(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    set_engine_for_testing(engine)

    original_routes = list(app.router.routes)
    reset_metrics_collector()
    try:
        for _ in range(2):  # zweiter Start: nichts doppelt, kein neuer Protokolleintrag
            async with lifespan(app):
                pass
        async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
            assert len((await verify.execute(select(Host))).scalars().all()) == 5
            assert len((await verify.execute(select(Notification))).scalars().all()) == 4
            actions = [r.action for r in (await verify.execute(select(AuditEntry))).scalars().all()]
            assert actions.count("system.demo.seeded") == 1

        # Mit echtem Server bleibt alles, wie es ist -- der Start laeuft trotzdem durch.
        async with async_sessionmaker(engine, expire_on_commit=False)() as setup:
            await remove_demo_data(setup)
            await hosts_service.create_host(setup, name="echt", address="10.0.0.5")
            await setup.commit()
        async with lifespan(app):
            pass
        async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
            assert [h.name for h in (await verify.execute(select(Host))).scalars().all()] == ["echt"]
    finally:
        app.router.routes[:] = original_routes
        await engine.dispose()
        reset_engine_cache()
        reset_scheduler_service()
        reset_metrics_collector()
        reset_event_bus()


# --- Beispiel-Apps ("+ App hinzufuegen") -------------------------------------------------------------


@pytest.mark.asyncio
async def test_seeds_a_few_demo_apps_from_the_documentation_range(db_session):
    status, _ = await seed_demo_data(db_session)
    assert status.apps == 3

    apps = (await db_session.execute(select(CustomApp).order_by(CustomApp.sort_order))).scalars().all()
    assert 2 <= len(apps) <= 3
    hosts_by_id = {h.id: h for h in (await db_session.execute(select(Host))).scalars().all()}
    for app in apps:
        assert app.url.startswith("http://192.0.2."), "nur RFC-5737-Adressen: nie ein echtes Netz"
        assert ipaddress.ip_address(app.url.split("//", 1)[1].split("/")[0].split(":")[0]) in ipaddress.ip_network("192.0.2.0/24")
        assert app.name.startswith("Beispiel")
        assert app.group_name and app.icon
        # Server-Bezug zeigt auf einen Beispiel-Server (oder ist leer), nie auf etwas anderes.
        assert app.host_id is None or app.host_id in hosts_by_id
        # Nur Inhalte, die die normale Pruefung auch beim Anlegen durchliesse -- und kein Fremdwort aus dem Kern-Waechter.
        assert custom_apps_service.clean_url(app.url) == app.url
        assert custom_apps_service.clean_icon(app.icon) == app.icon
        assert "docker" not in f"{app.name} {app.group_name}".lower() and "proxmox" not in f"{app.name} {app.group_name}".lower()
    assert [a.sort_order for a in apps] == list(range(len(apps)))


@pytest.mark.asyncio
async def test_removing_demo_data_removes_the_demo_apps_and_nothing_else(db_session):
    await seed_demo_data(db_session)
    mine = CustomApp(name="Mein Router", url="http://192.168.2.1", sort_order=10)
    db_session.add(mine)
    await db_session.flush()

    removal = await remove_demo_data(db_session)
    assert removal.removed_apps == 3 and removal.kept_apps == 0
    left = (await db_session.execute(select(CustomApp))).scalars().all()
    assert [a.name for a in left] == ["Mein Router"]
    assert (await demo_seed.demo_status(db_session)).apps == 0


@pytest.mark.asyncio
async def test_a_demo_app_that_became_real_is_kept(db_session):
    """Hat jemand eine Beispiel-App auf eine echte Adresse umgestellt, bleibt sie stehen (wie beim Server)."""
    await seed_demo_data(db_session)
    app = (await db_session.execute(select(CustomApp).order_by(CustomApp.sort_order))).scalars().first()
    app.url = "http://192.168.2.1"
    await db_session.flush()

    removal = await remove_demo_data(db_session)
    assert removal.removed_apps == 2 and removal.kept_apps == 1
    left = (await db_session.execute(select(CustomApp))).scalars().all()
    assert [a.url for a in left] == ["http://192.168.2.1"]
    assert (await demo_seed.demo_status(db_session)).active is False


@pytest.mark.asyncio
async def test_removing_the_demo_hosts_leaves_no_dangling_link_on_a_kept_app(db_session):
    await seed_demo_data(db_session)
    linked = (await db_session.execute(select(CustomApp).where(CustomApp.host_id.is_not(None)))).scalars().first()
    assert linked is not None, "mindestens eine Beispiel-App hat einen Beispiel-Server als Bezug"
    linked.url = "http://192.168.2.50"  # echt geworden
    await db_session.flush()

    await remove_demo_data(db_session)
    await db_session.refresh(linked)
    assert linked.host_id is None


@pytest.mark.asyncio
async def test_leftover_demo_apps_do_not_keep_the_demo_active_and_a_new_seed_clears_them_first(db_session):
    """Alles von Hand geloescht ausser den Apps: kein Band mehr; ein neues Anlegen raeumt die Reste zuerst weg."""
    await seed_demo_data(db_session)
    for host in (await db_session.execute(select(Host))).scalars().all():
        await hosts_service.delete_host(db_session, host.id)
    await db_session.execute(Notification.__table__.delete())
    await db_session.flush()

    status = await demo_status_of(db_session)
    assert status.active is False and status.hosts == 0 and status.apps == 3

    again, created = await seed_demo_data(db_session)
    assert created is True and again.apps == 3
    assert len((await db_session.execute(select(CustomApp))).scalars().all()) == 3, "nicht sechs"


@pytest.mark.asyncio
async def test_demo_apps_come_behind_apps_the_user_already_has(db_session):
    """Eigene Apps kann man ohne Server anlegen. Die Beispiel-Apps hängen sich hinten an, statt sich mit
    Positionen 0-2 zwischen die eigenen zu mischen."""
    for name in ("Zeta", "Alpha", "Mitte"):
        await custom_apps_service.create_app(db_session, user_id=None, name=name, url="http://192.168.2.2")

    status, created = await seed_demo_data(db_session)
    assert created is True and status.apps == 3

    listed = await custom_apps_service.list_apps(db_session)
    assert [a.name for a in listed[:3]] == ["Zeta", "Alpha", "Mitte"]
    assert all(a.name.startswith("Beispiel") for a in listed[3:]) and len(listed) == 6
    assert [a.sort_order for a in listed] == sorted({a.sort_order for a in listed}) == list(range(6))


@pytest.mark.asyncio
async def test_new_demo_apps_come_behind_a_demo_app_that_was_kept(db_session):
    """Eine auf eine echte Adresse umgestellte Beispiel-App bleibt mit ihrer alten Position stehen -- die neuen
    Beispiel-Apps kommen dahinter und nicht mit denselben Positionen 0-2 daneben."""
    await seed_demo_data(db_session)
    nas = (await db_session.execute(select(CustomApp).where(CustomApp.name == "Beispiel-NAS"))).scalar_one()
    nas.url = "http://192.168.2.20:5000"
    nas.name = "Mein NAS"
    await db_session.flush()
    removal = await remove_demo_data(db_session)
    assert removal.kept_apps == 1 and removal.removed_apps == 2

    await seed_demo_data(db_session)
    listed = await custom_apps_service.list_apps(db_session)
    assert listed[0].name == "Mein NAS"
    assert len(listed) == 4 and all(a.name.startswith("Beispiel") for a in listed[1:])
    assert len({a.sort_order for a in listed}) == 4, "keine doppelten Positionen"


@pytest.mark.asyncio
async def test_demo_apps_respect_the_maximum_number_of_apps(db_session):
    """Auch die Beispieldaten überschreiten die Obergrenze nicht (sonst scheitert danach das Verschieben)."""
    for i in range(custom_apps_service.MAX_APPS - 1):
        db_session.add(CustomApp(name=f"App {i:03d}", url="http://192.168.2.2", sort_order=i))
    await db_session.flush()

    status, created = await seed_demo_data(db_session)
    assert created is True
    assert status.apps == 1
    assert len((await db_session.execute(select(CustomApp))).scalars().all()) == custom_apps_service.MAX_APPS

    removal = await remove_demo_data(db_session)
    assert removal.removed_apps == 1
    assert len((await db_session.execute(select(CustomApp))).scalars().all()) == custom_apps_service.MAX_APPS - 1


@pytest.mark.asyncio
async def test_demo_apps_are_skipped_when_the_maximum_is_already_reached(db_session):
    for i in range(custom_apps_service.MAX_APPS):
        db_session.add(CustomApp(name=f"App {i:03d}", url="http://192.168.2.2", sort_order=i))
    await db_session.flush()

    status, created = await seed_demo_data(db_session)
    assert created is True and status.hosts == 5 and status.apps == 0
    assert len((await db_session.execute(select(CustomApp))).scalars().all()) == custom_apps_service.MAX_APPS
