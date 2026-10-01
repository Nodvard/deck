"""Beispieldaten: `GET /demo`, `POST /demo/seed`, `DELETE /demo` (core/demo_seed.py, docs/04-API.md)."""

from __future__ import annotations

import pytest
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import (
    AuditEntry,
    DashboardLayout,
    Host,
    Notification,
    Secret,
    Setting,
)
from nodvard_sdk import GridSize, ListItem, ListView, WidgetSpec
from sqlalchemy import func, select


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _user_token(client, db_session, username: str, *, role: str | None = None, permissions: tuple[str, ...] = ()):
    from nodvard_deck.core import security
    from nodvard_deck.models import Role, RolePermission, User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password("whatever123"), is_active=True)
    if role is not None:
        user.roles.append(roles[role])
    if permissions:
        custom = Role(name=f"rolle-{username}", permissions=[RolePermission(permission=p) for p in permissions])
        db_session.add(custom)
        user.roles.append(custom)
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": "whatever123"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _register_widgets(*ids: str, permissions: list[str] | None = None) -> None:
    for i, widget_id in enumerate(ids):
        get_extension_runtime().ui.register_widget(
            "beispiel-modul",
            WidgetSpec(
                id=widget_id, title=widget_id, data_endpoint="w", size=GridSize(w=4 + 4 * (i % 2), h=3),
                view=ListView(item=ListItem(title="{{ t }}")), permissions=permissions or [],
            ),
        )


async def _count(db_session, model) -> int:
    return (await db_session.execute(select(func.count()).select_from(model))).scalar_one()


async def _audit_actions(db_session) -> list[str]:
    rows = (await db_session.execute(select(AuditEntry).order_by(AuditEntry.ts, AuditEntry.id))).scalars().all()
    return [r.action for r in rows if r.action.startswith("system.demo.")]


@pytest.mark.asyncio
async def test_requires_login(client):
    assert (await client.get("/api/v1/demo")).status_code == 401
    assert (await client.post("/api/v1/demo/seed")).status_code == 401
    assert (await client.delete("/api/v1/demo")).status_code == 401


@pytest.mark.asyncio
async def test_status_without_demo_data(client):
    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/demo", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {
        "active": False, "hosts": 0, "notifications": 0, "layout_replaced": False, "hosts_with_access": [], "apps": 0,
    }


@pytest.mark.asyncio
async def test_seed_creates_hosts_and_notifications_and_status_reports_them(client, db_session):
    token = await _bootstrap_owner(client)
    r = await client.post("/api/v1/demo/seed", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True and body["active"] is True
    assert 3 <= body["hosts"] <= 5
    assert body["notifications"] >= 3

    status_ = (await client.get("/api/v1/demo", headers=_auth(token))).json()
    assert status_["active"] is True and status_["hosts"] == body["hosts"]

    hosts = (await client.get("/api/v1/hosts", headers=_auth(token))).json()
    assert len(hosts) == body["hosts"]
    assert {h["status"] for h in hosts} >= {"up", "down"}  # nicht alles nur gruen
    for h in hosts:
        assert h["address"].startswith("192.0.2."), "nur RFC-5737-Adressen, nie ein echtes Netz"
        assert h["credential"] is None, "ohne Zugang: nichts, was sich per SSH verbinden koennte"
        assert "demo" in h["tags"]

    notes = (await db_session.execute(select(Notification))).scalars().all()
    assert {n.severity for n in notes} == {"info", "warning", "critical"}
    for n in notes:
        assert n.payload["path"].startswith("/")
        assert n.title.startswith("Beispiel:")
    assert any(n.read_at is None for n in notes)


@pytest.mark.asyncio
async def test_seed_does_not_deliver_to_channels(client, monkeypatch):
    """Beispiel-Meldungen duerfen nie als echte Push-Nachricht rausgehen."""
    from nodvard_deck.services import notifications as notifications_service

    async def boom(*args, **kwargs):
        raise AssertionError("deliver() darf fuer Beispieldaten nicht laufen")

    monkeypatch.setattr(notifications_service, "deliver", boom)
    token = await _bootstrap_owner(client)
    assert (await client.post("/api/v1/demo/seed", headers=_auth(token))).status_code == 200


@pytest.mark.asyncio
async def test_seed_is_idempotent(client, db_session):
    token = await _bootstrap_owner(client)
    first = (await client.post("/api/v1/demo/seed", headers=_auth(token))).json()
    second = await client.post("/api/v1/demo/seed", headers=_auth(token))
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["hosts"] == first["hosts"]
    assert await _count(db_session, Host) == first["hosts"]
    assert await _count(db_session, Notification) == first["notifications"]
    assert await _audit_actions(db_session) == ["system.demo.seeded"]  # nur das erste Mal protokolliert


@pytest.mark.asyncio
async def test_seed_is_refused_when_real_hosts_exist(client, db_session):
    token = await _bootstrap_owner(client)
    created = await client.post("/api/v1/hosts", json={"name": "echt", "address": "10.0.0.5"}, headers=_auth(token))
    assert created.status_code == 201
    r = await client.post("/api/v1/demo/seed", headers=_auth(token))
    assert r.status_code == 409
    assert "Server" in r.json()["detail"]
    assert await _count(db_session, Host) == 1
    assert await _count(db_session, Notification) == 0
    assert await _audit_actions(db_session) == []


@pytest.mark.asyncio
async def test_remove_deletes_exactly_the_demo_data_and_keeps_real_data(client, db_session):
    token = await _bootstrap_owner(client)
    # Eine echte Meldung VOR den Beispieldaten, eine danach, dazu ein echter Server danach.
    db_session.add(Notification(severity="warning", title="Echte Meldung vorher", body="x"))
    await db_session.commit()
    await client.post("/api/v1/demo/seed", headers=_auth(token))
    db_session.add(Notification(severity="critical", title="Echte Meldung nachher", body="y", payload={"path": "/"}))
    await db_session.commit()
    real = await client.post("/api/v1/hosts", json={"name": "mein-pi", "address": "192.168.2.72"}, headers=_auth(token))
    assert real.status_code == 201

    r = await client.delete("/api/v1/demo", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["removed_hosts"] >= 3 and body["removed_notifications"] >= 3
    assert body["kept_hosts"] == 0

    hosts = (await db_session.execute(select(Host))).scalars().all()
    assert [h.name for h in hosts] == ["mein-pi"]
    titles = sorted(n.title for n in (await db_session.execute(select(Notification))).scalars().all())
    assert titles == ["Echte Meldung nachher", "Echte Meldung vorher"]
    assert (await client.get("/api/v1/demo", headers=_auth(token))).json()["active"] is False
    leftover = await db_session.get(Setting, ("demo.seeded_ids", "global", ""))
    assert leftover is None
    assert await _audit_actions(db_session) == ["system.demo.seeded", "system.demo.removed"]


@pytest.mark.asyncio
async def test_remove_without_demo_data_is_a_harmless_noop(client, db_session):
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/hosts", json={"name": "echt", "address": "10.0.0.5"}, headers=_auth(token))
    r = await client.delete("/api/v1/demo", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {
        "removed_hosts": 0, "kept_hosts": 0, "removed_notifications": 0, "layout_restored": False,
        "removed_apps": 0, "kept_apps": 0,
    }
    assert await _count(db_session, Host) == 1
    assert await _audit_actions(db_session) == []


@pytest.mark.asyncio
async def test_remove_never_touches_a_host_that_became_real(client, db_session):
    """Hat jemand einen Beispiel-Server zu einem echten umgebaut (echte Adresse), bleibt er stehen."""
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/demo/seed", headers=_auth(token))
    hosts = (await client.get("/api/v1/hosts", headers=_auth(token))).json()
    adopted = next(h for h in hosts if h["name"] == "demo-nas")
    patched = await client.patch(f"/api/v1/hosts/{adopted['id']}", json={"address": "192.168.2.50"}, headers=_auth(token))
    assert patched.status_code == 200

    status_ = (await client.get("/api/v1/demo", headers=_auth(token))).json()
    assert status_["hosts"] == len(hosts) - 1  # zaehlt nicht mehr als Beispiel
    removed = (await client.delete("/api/v1/demo", headers=_auth(token))).json()
    assert removed["kept_hosts"] == 1
    assert removed["removed_hosts"] == len(hosts) - 1
    remaining = (await db_session.execute(select(Host))).scalars().all()
    assert [h.name for h in remaining] == ["demo-nas"]


@pytest.mark.asyncio
async def test_remove_also_deletes_demo_host_with_credential_but_status_warns_before(client, db_session):
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/demo/seed", headers=_auth(token))
    hosts = (await client.get("/api/v1/hosts", headers=_auth(token))).json()
    pi = next(h for h in hosts if h["name"] == "demo-pi")
    cred = await client.post(
        f"/api/v1/hosts/{pi['id']}/credentials",
        json={"kind": "ssh_password", "username": "pi", "port": 22, "secret_value": "geheim123"},
        headers=_auth(token),
    )
    assert cred.status_code == 201, cred.text

    status_ = (await client.get("/api/v1/demo", headers=_auth(token))).json()
    assert status_["hosts_with_access"] == [pi["display_name"]]

    assert (await client.delete("/api/v1/demo", headers=_auth(token))).status_code == 200
    assert await _count(db_session, Host) == 0
    assert await _count(db_session, Secret) == 0  # auch das Geheimnis ist weg, nichts bleibt zurueck


@pytest.mark.asyncio
async def test_remove_refused_while_an_action_runs_on_a_demo_host_changes_nothing(client, db_session):
    from nodvard_deck.models import Action

    token = await _bootstrap_owner(client)
    await client.post("/api/v1/demo/seed", headers=_auth(token))
    hosts = (await db_session.execute(select(Host).order_by(Host.name))).scalars().all()
    db_session.add(Action(
        ext_id="x", action_type="t", host_id=hosts[-1].id, status="executing", proposed_by_type="user",
        proposed_by_id="u", reason="test",
    ))
    await db_session.commit()

    r = await client.delete("/api/v1/demo", headers=_auth(token))
    assert r.status_code == 409
    assert await _count(db_session, Host) == len(hosts)
    assert await _count(db_session, Notification) >= 3
    assert (await client.get("/api/v1/demo", headers=_auth(token))).json()["active"] is True


@pytest.mark.asyncio
async def test_seed_again_after_remove_works(client, db_session):
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/demo/seed", headers=_auth(token))
    await client.delete("/api/v1/demo", headers=_auth(token))
    again = await client.post("/api/v1/demo/seed", headers=_auth(token))
    assert again.status_code == 200 and again.json()["created"] is True


@pytest.mark.asyncio
async def test_seed_after_everything_was_deleted_by_hand_starts_clean(client, db_session):
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/demo/seed", headers=_auth(token))
    for host in (await client.get("/api/v1/hosts", headers=_auth(token))).json():
        await client.delete(f"/api/v1/hosts/{host['id']}", headers=_auth(token))
    for n in (await db_session.execute(select(Notification))).scalars().all():
        await db_session.delete(n)
    await db_session.commit()
    assert (await client.get("/api/v1/demo", headers=_auth(token))).json()["active"] is False
    again = await client.post("/api/v1/demo/seed", headers=_auth(token))
    assert again.status_code == 200 and again.json()["created"] is True


# --- Layout ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_layout_is_replaced_and_restored_on_remove(client, db_session):
    token = await _bootstrap_owner(client)
    _register_widgets("eins", "zwei", "drei")
    layout = (await client.get("/api/v1/dashboard/layouts", headers=_auth(token))).json()[0]
    mine = [{"widget_id": "eins", "ext_id": "beispiel-modul", "x": 3, "y": 7, "w": 2, "h": 2, "config": {"hidden": True}}]
    await client.put(f"/api/v1/dashboard/layouts/{layout['id']}", json={"name": "Meins", "items": mine}, headers=_auth(token))

    seeded = (await client.post("/api/v1/demo/seed", headers=_auth(token))).json()
    assert seeded["layout_replaced"] is True
    demo_layout = (await client.get(f"/api/v1/dashboard/layouts/{layout['id']}", headers=_auth(token))).json()
    assert [i["widget_id"] for i in demo_layout["items"]] == ["eins", "zwei", "drei"]
    assert all(i["config"] == {} for i in demo_layout["items"])  # nichts ausgeblendet
    for i in demo_layout["items"]:
        assert 0 <= i["x"] and i["x"] + i["w"] <= 12
    assert demo_layout["name"] == "Meins"

    removed = (await client.delete("/api/v1/demo", headers=_auth(token))).json()
    assert removed["layout_restored"] is True
    restored = (await client.get(f"/api/v1/dashboard/layouts/{layout['id']}", headers=_auth(token))).json()
    assert restored["items"] == mine


@pytest.mark.asyncio
async def test_layout_untouched_without_widgets(client, db_session):
    token = await _bootstrap_owner(client)
    layout = (await client.get("/api/v1/dashboard/layouts", headers=_auth(token))).json()[0]
    seeded = (await client.post("/api/v1/demo/seed", headers=_auth(token))).json()
    assert seeded["layout_replaced"] is False
    removed = (await client.delete("/api/v1/demo", headers=_auth(token))).json()
    assert removed["layout_restored"] is False
    after = (await db_session.execute(select(DashboardLayout))).scalars().all()
    assert [l.id for l in after] == [layout["id"]] and after[0].items == []


@pytest.mark.asyncio
async def test_layout_only_uses_widgets_the_user_may_see(client, db_session):
    token = await _bootstrap_owner(client)
    _register_widgets("offen")
    _register_widgets("geheim", permissions=["secrets.read"])
    admin_like = await _user_token(client, db_session, "adm", permissions=("hosts.write", "settings.write", "hosts.read"))
    await client.post("/api/v1/demo/seed", headers=_auth(admin_like))
    layouts = (await client.get("/api/v1/dashboard/layouts", headers=_auth(admin_like))).json()
    assert [i["widget_id"] for i in layouts[0]["items"]] == ["offen"]
    del token


# --- Rechte ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_permissions(client, db_session):
    owner = await _bootstrap_owner(client)
    viewer = await _user_token(client, db_session, "gast", role="viewer")
    operator = await _user_token(client, db_session, "bediener", role="operator")
    hosts_only = await _user_token(client, db_session, "nurhosts", permissions=("hosts.read", "hosts.write"))
    settings_only = await _user_token(client, db_session, "nureinst", permissions=("hosts.read", "settings.write"))
    admin = await _user_token(client, db_session, "verwalter", role="admin")

    for token in (viewer, operator, hosts_only, settings_only):
        assert (await client.post("/api/v1/demo/seed", headers=_auth(token))).status_code == 403
        assert (await client.delete("/api/v1/demo", headers=_auth(token))).status_code == 403
    assert await _count(db_session, Host) == 0

    # Lesen duerfen alle, die Server sehen duerfen (fuer das Band) ...
    for token in (viewer, operator, hosts_only):
        assert (await client.get("/api/v1/demo", headers=_auth(token))).status_code == 200
    # ... wer keine Server sehen darf, nicht.
    nobody = await _user_token(client, db_session, "keinrecht", permissions=("audit.read",))
    assert (await client.get("/api/v1/demo", headers=_auth(nobody))).status_code == 403

    assert (await client.post("/api/v1/demo/seed", headers=_auth(admin))).status_code == 200
    assert (await client.delete("/api/v1/demo", headers=_auth(owner))).status_code == 200


@pytest.mark.asyncio
async def test_audit_entries_carry_the_user(client, db_session):
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/demo/seed", headers=_auth(token))
    await client.delete("/api/v1/demo", headers=_auth(token))
    rows = (await db_session.execute(select(AuditEntry).where(AuditEntry.action.like("system.demo.%")))).scalars().all()
    by_action = {r.action: r for r in rows}
    assert set(by_action) == {"system.demo.seeded", "system.demo.removed"}
    assert all(r.actor_type == "user" and r.outcome == "success" for r in rows)
    assert by_action["system.demo.seeded"].detail["hosts"] >= 3
    assert by_action["system.demo.removed"].detail["hosts"] >= 3
    # ueber die Protokoll-Seite auffindbar
    listed = await client.get("/api/v1/audit", params={"action": "system.demo.seeded"}, headers=_auth(token))
    assert listed.status_code == 200 and len(listed.json()) == 1


@pytest.mark.asyncio
async def test_demo_apps_show_up_in_the_cockpit_overview_and_disappear_with_the_demo_data(client, db_session):
    from nodvard_deck.api.v1 import overview as overview_api
    from nodvard_deck.models import CustomApp

    overview_api.reset_cache()
    token = await _bootstrap_owner(client)
    seeded = (await client.post("/api/v1/demo/seed", headers=_auth(token))).json()
    assert seeded["apps"] == 3
    assert (await client.get("/api/v1/demo", headers=_auth(token))).json()["apps"] == 3

    apps = (await client.get("/api/v1/overview", headers=_auth(token))).json()["apps"]
    assert len(apps) == 3 and {a["source"] for a in apps} == {"custom"}
    assert all(a["url"].startswith("http://192.0.2.") for a in apps)

    removed = (await client.delete("/api/v1/demo", headers=_auth(token))).json()
    assert removed["removed_apps"] == 3 and removed["kept_apps"] == 0
    assert (await db_session.execute(select(func.count()).select_from(CustomApp))).scalar_one() == 0
    assert (await client.get("/api/v1/overview", headers=_auth(token))).json()["apps"] == []
    overview_api.reset_cache()
