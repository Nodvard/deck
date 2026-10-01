"""Eigene App-Kacheln ("+ App hinzufuegen"): `GET/POST /apps`, `PATCH/DELETE /apps/{id}`,
`PUT /apps/order` und die Zusammenfuehrung mit den erkannten Diensten in `GET /overview`
(api/v1/apps.py, services/custom_apps.py, docs/04-API.md).

Der Server ruft die Adressen nie selbst ab -- deshalb kein Netzwerk, kein SSH und keine Attrappe
dafuer in diesen Tests."""

from __future__ import annotations

import pytest
from nodvard_deck.api.v1 import overview as overview_api
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import AuditEntry, CustomApp, Host
from nodvard_deck.services import custom_apps as custom_apps_service
from nodvard_sdk import ServiceCatalog
from sqlalchemy import func, select

PASSWORD = "whatever123"


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
    return login.json()["access_token"]


async def _create(client, token, **fields):
    body = {"name": "Router", "url": "http://192.168.2.1"} | fields
    return await client.post("/api/v1/apps", json=body, headers=_auth(token))


async def _audit(db_session, prefix="app."):
    rows = (await db_session.execute(select(AuditEntry).order_by(AuditEntry.ts, AuditEntry.id))).scalars().all()
    return [r for r in rows if r.action.startswith(prefix)]


async def _count(db_session) -> int:
    return (await db_session.execute(select(func.count()).select_from(CustomApp))).scalar_one()


@pytest.fixture(autouse=True)
def _fresh_overview_cache():
    overview_api.reset_cache()
    yield
    runtime = get_extension_runtime()
    for ext_id in ("fake-services",):
        runtime.capabilities.clear_extension(ext_id)
    overview_api.reset_cache()


# --- Anmeldung und Rechte -------------------------------------------------------


@pytest.mark.asyncio
async def test_every_endpoint_needs_a_login(client):
    assert (await client.get("/api/v1/apps")).status_code == 401
    assert (await client.post("/api/v1/apps", json={"name": "x", "url": "http://x.example"})).status_code == 401
    assert (await client.patch("/api/v1/apps/abc", json={"name": "y"})).status_code == 401
    assert (await client.delete("/api/v1/apps/abc")).status_code == 401
    assert (await client.put("/api/v1/apps/order", json={"ids": []})).status_code == 401


@pytest.mark.asyncio
async def test_viewer_may_read_but_not_write(client, db_session):
    owner = await _bootstrap_owner(client)
    created = (await _create(client, owner)).json()
    viewer = await _user_token(client, db_session, "betrachter", role="viewer")

    listed = await client.get("/api/v1/apps", headers=_auth(viewer))
    assert listed.status_code == 200
    assert [a["name"] for a in listed.json()] == ["Router"]

    denied = [
        await _create(client, viewer),
        await client.patch(f"/api/v1/apps/{created['id']}", json={"name": "Neu"}, headers=_auth(viewer)),
        await client.delete(f"/api/v1/apps/{created['id']}", headers=_auth(viewer)),
        await client.put("/api/v1/apps/order", json={"ids": [created["id"]]}, headers=_auth(viewer)),
    ]
    for response in denied:
        assert response.status_code == 403, response.text
        assert "apps.write" in response.json()["detail"]
    assert await _count(db_session) == 1
    assert [e.action for e in await _audit(db_session)] == ["app.created"], "nur das Anlegen des Owners, die abgelehnten Versuche nicht"


@pytest.mark.asyncio
async def test_operator_does_not_get_apps_write_by_default(client, db_session):
    """Die Links stehen allen Nutzern auf der Startseite -- ein Bediener bekommt das Recht nicht von selbst."""
    await _bootstrap_owner(client)
    operator = await _user_token(client, db_session, "bediener", role="operator")
    assert (await client.get("/api/v1/apps", headers=_auth(operator))).status_code == 200
    assert (await _create(client, operator)).status_code == 403


@pytest.mark.asyncio
async def test_apps_write_alone_can_be_granted_via_a_custom_role(client, db_session):
    await _bootstrap_owner(client)
    token = await _user_token(client, db_session, "pflegerin", permissions=("apps.write",))
    created = await _create(client, token)
    assert created.status_code == 201, created.text
    # Schreiben ist nicht Lesen: ohne hosts.read bleibt die Liste zu.
    assert (await client.get("/api/v1/apps", headers=_auth(token))).status_code == 403
    assert (await client.patch(f"/api/v1/apps/{created.json()['id']}", json={"name": "Neu"}, headers=_auth(token))).status_code == 200
    assert (await client.delete(f"/api/v1/apps/{created.json()['id']}", headers=_auth(token))).status_code == 204


@pytest.mark.asyncio
async def test_user_without_hosts_read_cannot_list(client, db_session):
    await _bootstrap_owner(client)
    token = await _user_token(client, db_session, "nur-meldungen", permissions=("notifications.read",))
    assert (await client.get("/api/v1/apps", headers=_auth(token))).status_code == 403


# --- Anlegen, Lesen, Aendern, Loeschen ------------------------------------------


@pytest.mark.asyncio
async def test_create_returns_the_normalised_app(client, db_session):
    token = await _bootstrap_owner(client)
    r = await _create(
        client, token, name="  Pi-hole  ", url="  http://192.168.2.72/admin ", icon="shield-check", color="#3B82F6",
        group="  Netzwerk ",
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Pi-hole"
    assert body["url"] == "http://192.168.2.72/admin"
    assert body["icon"] == "shield-check"
    assert body["color"] == "#3b82f6"
    assert body["group"] == "Netzwerk"
    assert body["open_in_new_tab"] is True
    assert body["host_id"] is None and body["host"] is None
    assert isinstance(body["id"], str) and body["id"]
    assert body["sort_order"] == 0
    assert body["created_at"] and body["updated_at"]

    row = await db_session.get(CustomApp, body["id"])
    assert row is not None and row.name == "Pi-hole" and row.created_by_user_id is not None


@pytest.mark.asyncio
async def test_create_with_only_name_and_url_uses_defaults(client):
    token = await _bootstrap_owner(client)
    body = (await _create(client, token)).json()
    assert body["icon"] is None and body["color"] is None and body["group"] is None
    assert body["open_in_new_tab"] is True


@pytest.mark.asyncio
async def test_new_apps_are_appended_at_the_end(client):
    token = await _bootstrap_owner(client)
    first = (await _create(client, token, name="Eins")).json()
    second = (await _create(client, token, name="Zwei")).json()
    third = (await _create(client, token, name="Drei")).json()
    assert [first["sort_order"], second["sort_order"], third["sort_order"]] == [0, 1, 2]
    listed = (await client.get("/api/v1/apps", headers=_auth(token))).json()
    assert [a["name"] for a in listed] == ["Eins", "Zwei", "Drei"]


@pytest.mark.asyncio
async def test_list_is_ordered_by_sort_order_then_name(client):
    token = await _bootstrap_owner(client)
    b = (await _create(client, token, name="B")).json()
    a = (await _create(client, token, name="A")).json()
    c = (await _create(client, token, name="C")).json()
    await client.patch(f"/api/v1/apps/{c['id']}", json={"sort_order": 0}, headers=_auth(token))
    await client.patch(f"/api/v1/apps/{b['id']}", json={"sort_order": 5}, headers=_auth(token))
    await client.patch(f"/api/v1/apps/{a['id']}", json={"sort_order": 5}, headers=_auth(token))
    listed = (await client.get("/api/v1/apps", headers=_auth(token))).json()
    assert [x["name"] for x in listed] == ["C", "A", "B"], "gleiche Position: nach Namen"


@pytest.mark.asyncio
async def test_patch_changes_only_the_given_fields(client):
    token = await _bootstrap_owner(client)
    app = (await _create(client, token, icon="globe", color="#10b981", group="Netz", open_in_new_tab=True)).json()
    r = await client.patch(f"/api/v1/apps/{app['id']}", json={"name": "Neuer Name"}, headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Neuer Name"
    assert (body["url"], body["icon"], body["color"], body["group"]) == (app["url"], "globe", "#10b981", "Netz")


@pytest.mark.asyncio
async def test_patch_can_clear_optional_fields_with_null(client):
    token = await _bootstrap_owner(client)
    host = await client.post("/api/v1/hosts", json={"name": "nas", "address": "192.168.2.20"}, headers=_auth(token))
    app = (await _create(client, token, icon="globe", color="#10b981", group="Netz", host_id=host.json()["id"])).json()
    assert app["host_id"] == host.json()["id"]
    r = await client.patch(
        f"/api/v1/apps/{app['id']}",
        json={"icon": None, "color": None, "group": None, "host_id": None, "open_in_new_tab": False},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["icon"], body["color"], body["group"], body["host_id"], body["host"]) == (None, None, None, None, None)
    assert body["open_in_new_tab"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["name", "url", "open_in_new_tab", "sort_order"])
async def test_patch_does_not_accept_null_for_required_fields(client, field):
    token = await _bootstrap_owner(client)
    app = (await _create(client, token)).json()
    r = await client.patch(f"/api/v1/apps/{app['id']}", json={field: None}, headers=_auth(token))
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_patch_and_delete_unknown_app_is_404(client):
    token = await _bootstrap_owner(client)
    assert (await client.patch("/api/v1/apps/gibt-es-nicht", json={"name": "x"}, headers=_auth(token))).status_code == 404
    assert (await client.delete("/api/v1/apps/gibt-es-nicht", headers=_auth(token))).status_code == 404


@pytest.mark.asyncio
async def test_delete_removes_the_app(client, db_session):
    token = await _bootstrap_owner(client)
    app = (await _create(client, token)).json()
    r = await client.delete(f"/api/v1/apps/{app['id']}", headers=_auth(token))
    assert r.status_code == 204
    assert await _count(db_session) == 0
    assert (await client.get("/api/v1/apps", headers=_auth(token))).json() == []
    assert (await client.delete(f"/api/v1/apps/{app['id']}", headers=_auth(token))).status_code == 404


# --- Pruefung der Eingaben --------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(document.cookie)",
        "JAVASCRIPT:alert(1)",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "file:///etc/passwd",
        "ftp://nas.local/",
        "vbscript:x",
        "192.168.2.1",
        "//evil.example/",
        "http://user:pass@192.168.2.1/",
        "http://exa mple.org/",
        "http://",
        "http://host:99999/",
        "http://example.org/" + "a" * 1100,
        "",
        "   ",
    ],
)
async def test_create_rejects_unsafe_or_broken_urls(client, db_session, url):
    token = await _bootstrap_owner(client)
    r = await _create(client, token, url=url)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail[0]["loc"][-1] == "url"
    assert not detail[0]["msg"].startswith("Value error"), "deutsche Meldung ohne englisches Praefix"
    assert "input" not in detail[0] and "ctx" not in detail[0], "die Eingabe kommt nicht in der Antwort zurueck"
    assert await _count(db_session) == 0
    assert await _audit(db_session) == []


@pytest.mark.asyncio
async def test_patch_rejects_an_unsafe_url_and_keeps_the_old_one(client, db_session):
    token = await _bootstrap_owner(client)
    app = (await _create(client, token, url="http://192.168.2.1")).json()
    r = await client.patch(f"/api/v1/apps/{app['id']}", json={"url": "javascript:alert(1)"}, headers=_auth(token))
    assert r.status_code == 422
    row = await db_session.get(CustomApp, app["id"])
    await db_session.refresh(row)
    assert row.url == "http://192.168.2.1"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Router\u2028Admin", "Router\u2029Admin", "Router\u00a0Admin", "Router\U0010ffffAdmin", "\u3164", "\u200d"])
async def test_create_rejects_names_with_line_separators_or_without_a_visible_character(client, db_session, name):
    token = await _bootstrap_owner(client)
    r = await _create(client, token, name=name)
    assert r.status_code == 422, r.text
    assert r.json()["detail"][0]["loc"][-1] == "name"
    assert await _count(db_session) == 0


@pytest.mark.asyncio
async def test_create_rejects_a_group_with_a_line_separator_and_an_icon_that_is_no_character(client, db_session):
    token = await _bootstrap_owner(client)
    group = await _create(client, token, group="Netz\u2028werk")
    assert group.status_code == 422 and group.json()["detail"][0]["loc"][-1] == "group"
    icon = await _create(client, token, icon="\U0010ffff")
    assert icon.status_code == 422 and icon.json()["detail"][0]["loc"][-1] == "icon"
    assert await _count(db_session) == 0


@pytest.mark.asyncio
async def test_create_rejects_a_missing_name_or_url(client):
    token = await _bootstrap_owner(client)
    assert (await client.post("/api/v1/apps", json={"url": "http://x.example"}, headers=_auth(token))).status_code == 422
    assert (await client.post("/api/v1/apps", json={"name": "x"}, headers=_auth(token))).status_code == 422
    assert (await _create(client, token, name="   ")).status_code == 422
    assert (await _create(client, token, name="N" * 61)).status_code == 422
    assert (await _create(client, token, group="G" * 41)).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("icon", ["https://tracker.example/pixel.png", "data:image/svg+xml,<svg/>", "gibt-es-nicht", "Router", "abc"])
async def test_create_rejects_free_image_urls_and_unknown_icon_names(client, icon):
    token = await _bootstrap_owner(client)
    r = await _create(client, token, icon=icon)
    assert r.status_code == 422, r.text
    assert r.json()["detail"][0]["loc"][-1] == "icon"


@pytest.mark.asyncio
async def test_create_accepts_an_emoji_as_icon(client):
    token = await _bootstrap_owner(client)
    r = await _create(client, token, icon="🛜")
    assert r.status_code == 201, r.text
    assert r.json()["icon"] == "🛜"


@pytest.mark.asyncio
@pytest.mark.parametrize("color", ["red", "#12345", "url(x)", "#3b82f6; background:red"])
async def test_create_rejects_a_bad_color(client, color):
    token = await _bootstrap_owner(client)
    r = await _create(client, token, color=color)
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"][-1] == "color"


@pytest.mark.asyncio
async def test_empty_optional_strings_become_null(client):
    token = await _bootstrap_owner(client)
    body = (await _create(client, token, icon="  ", color="", group="   ")).json()
    assert (body["icon"], body["color"], body["group"]) == (None, None, None)


@pytest.mark.asyncio
async def test_sort_order_must_be_a_sane_number(client):
    token = await _bootstrap_owner(client)
    assert (await _create(client, token, sort_order=-1)).status_code == 422
    assert (await _create(client, token, sort_order=10**9)).status_code == 422
    assert (await _create(client, token, sort_order=7)).json()["sort_order"] == 7


@pytest.mark.asyncio
async def test_unknown_host_is_refused(client, db_session):
    token = await _bootstrap_owner(client)
    r = await _create(client, token, host_id="gibt-es-nicht")
    assert r.status_code == 422, r.text
    assert "Server" in str(r.json()["detail"])
    assert await _count(db_session) == 0


@pytest.mark.asyncio
async def test_there_is_a_maximum_number_of_apps(client, monkeypatch):
    monkeypatch.setattr(custom_apps_service, "MAX_APPS", 2)
    token = await _bootstrap_owner(client)
    assert (await _create(client, token, name="A")).status_code == 201
    assert (await _create(client, token, name="B")).status_code == 201
    r = await _create(client, token, name="C")
    assert r.status_code == 409
    assert "2" in r.json()["detail"]


# --- Server-Bezug ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_can_be_linked_to_a_host_and_shows_its_name(client):
    token = await _bootstrap_owner(client)
    host = (await client.post(
        "/api/v1/hosts", json={"name": "nas", "display_name": "Mein NAS", "address": "192.168.2.20"}, headers=_auth(token),
    )).json()
    app = (await _create(client, token, name="NAS-Oberfläche", url="http://192.168.2.20:5000", host_id=host["id"])).json()
    assert app["host_id"] == host["id"] and app["host"] == "Mein NAS"
    listed = (await client.get("/api/v1/apps", headers=_auth(token))).json()
    assert listed[0]["host"] == "Mein NAS"


@pytest.mark.asyncio
async def test_deleting_the_host_unlinks_the_app_but_keeps_it(client, db_session):
    token = await _bootstrap_owner(client)
    host = (await client.post("/api/v1/hosts", json={"name": "nas", "address": "192.168.2.20"}, headers=_auth(token))).json()
    app = (await _create(client, token, host_id=host["id"])).json()
    assert (await client.delete(f"/api/v1/hosts/{host['id']}", headers=_auth(token))).status_code == 204
    assert (await db_session.execute(select(func.count()).select_from(Host))).scalar_one() == 0
    listed = (await client.get("/api/v1/apps", headers=_auth(token))).json()
    assert [(a["id"], a["host_id"], a["host"]) for a in listed] == [(app["id"], None, None)]


# --- Reihenfolge ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_order_rewrites_positions_in_the_given_sequence(client):
    token = await _bootstrap_owner(client)
    ids = {n: (await _create(client, token, name=n)).json()["id"] for n in ("A", "B", "C")}
    r = await client.put("/api/v1/apps/order", json={"ids": [ids["C"], ids["A"], ids["B"]]}, headers=_auth(token))
    assert r.status_code == 200, r.text
    assert [a["name"] for a in r.json()] == ["C", "A", "B"]
    assert [a["sort_order"] for a in r.json()] == [0, 1, 2]
    listed = (await client.get("/api/v1/apps", headers=_auth(token))).json()
    assert [a["name"] for a in listed] == ["C", "A", "B"]


@pytest.mark.asyncio
async def test_order_with_a_partial_list_puts_the_rest_behind(client):
    token = await _bootstrap_owner(client)
    ids = {n: (await _create(client, token, name=n)).json()["id"] for n in ("A", "B", "C", "D")}
    r = await client.put("/api/v1/apps/order", json={"ids": [ids["C"]]}, headers=_auth(token))
    assert [a["name"] for a in r.json()] == ["C", "A", "B", "D"]


@pytest.mark.asyncio
async def test_order_rejects_unknown_and_duplicate_ids(client):
    token = await _bootstrap_owner(client)
    a = (await _create(client, token, name="A")).json()["id"]
    unknown = await client.put("/api/v1/apps/order", json={"ids": [a, "gibt-es-nicht"]}, headers=_auth(token))
    assert unknown.status_code == 404
    twice = await client.put("/api/v1/apps/order", json={"ids": [a, a]}, headers=_auth(token))
    assert twice.status_code == 422


def _rows(count: int, *, start: int = 0) -> list[CustomApp]:
    return [CustomApp(name=f"App {i:03d}", url=f"http://192.0.2.{i % 250 + 1}", sort_order=i) for i in range(start, start + count)]


@pytest.mark.asyncio
async def test_order_still_works_when_there_are_a_few_more_apps_than_the_maximum(client, db_session):
    """Mehr als `MAX_APPS` kann man herbeifuehren (Beispieldaten, zwei Anlegen gleichzeitig). Das Verschieben, das
    immer alle Apps schickt, darf dann nicht mit 422 scheitern."""
    token = await _bootstrap_owner(client)
    db_session.add_all(_rows(custom_apps_service.MAX_APPS + 1))
    await db_session.commit()
    listed = (await client.get("/api/v1/apps", headers=_auth(token))).json()
    assert len(listed) == custom_apps_service.MAX_APPS + 1

    ids = [a["id"] for a in reversed(listed)]
    r = await client.put("/api/v1/apps/order", json={"ids": ids}, headers=_auth(token))
    assert r.status_code == 200, r.text
    assert [a["id"] for a in r.json()] == ids
    assert [a["sort_order"] for a in r.json()] == list(range(len(ids)))


@pytest.mark.asyncio
async def test_order_with_far_too_many_ids_is_refused_in_german(client):
    token = await _bootstrap_owner(client)
    r = await client.put("/api/v1/apps/order", json={"ids": [f"id-{i}" for i in range(custom_apps_service.MAX_APPS + 51)]}, headers=_auth(token))
    assert r.status_code == 422
    detail = r.json()["detail"][0]
    assert detail["loc"][-1] == "ids"
    assert "should have" not in detail["msg"].lower() and "Reihenfolge" in detail["msg"]


# --- Protokoll ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_update_delete_and_reorder_are_audited(client, db_session):
    token = await _bootstrap_owner(client)
    app = (await _create(client, token, name="Router", url="https://router.lan:8443/admin?token=geheim123")).json()
    other = (await _create(client, token, name="NAS", url="http://nas.lan")).json()
    await client.patch(f"/api/v1/apps/{app['id']}", json={"name": "Router neu", "group": "Netz"}, headers=_auth(token))
    await client.put("/api/v1/apps/order", json={"ids": [other["id"], app["id"]]}, headers=_auth(token))
    await client.delete(f"/api/v1/apps/{app['id']}", headers=_auth(token))

    entries = await _audit(db_session)
    assert [e.action for e in entries] == ["app.created", "app.created", "app.updated", "app.reordered", "app.deleted"]
    assert all(e.outcome == "success" and e.actor_type == "user" for e in entries)

    created, _, updated, reordered, deleted = entries
    assert created.target_type == "app" and created.target_id == app["id"]
    assert created.detail["name"] == "Router"
    assert created.detail["url"] == "https://router.lan:8443", "nur Rechner und Port, nie Pfad oder Zugangsdaten"
    assert updated.target_id == app["id"] and sorted(updated.detail["changed"]) == ["group", "name"]
    assert reordered.detail["count"] == 2
    assert deleted.target_id == app["id"] and deleted.detail["name"] == "Router neu"

    dump = " ".join(str(e.detail) for e in entries)
    assert "geheim123" not in dump and "token=" not in dump


@pytest.mark.asyncio
async def test_a_patch_without_changes_writes_no_audit_entry(client, db_session):
    token = await _bootstrap_owner(client)
    app = (await _create(client, token, name="Router")).json()
    await client.patch(f"/api/v1/apps/{app['id']}", json={"name": "Router"}, headers=_auth(token))
    assert [e.action for e in await _audit(db_session)] == ["app.created"]


# --- Zusammenfuehrung in GET /overview ------------------------------------------------------------------


class _Services:
    def __init__(self) -> None:
        self.calls = 0

    async def list_services(self):
        self.calls += 1
        return [
            {"id": "h1:grafana", "name": "grafana", "host": "docker-host", "host_id": "h1", "state": "running", "tone": "good",
             "url": "http://10.0.0.5:3000", "image": "grafana/grafana"},
            {"id": "h1:redis", "name": "redis", "host": "docker-host", "host_id": "h1", "state": "exited", "tone": "danger",
             "url": None, "image": "redis:7"},
        ]


@pytest.mark.asyncio
async def test_overview_merges_custom_apps_with_detected_services(client):
    token = await _bootstrap_owner(client)
    host = (await client.post(
        "/api/v1/hosts", json={"name": "nas", "display_name": "Mein NAS", "address": "192.168.2.20"}, headers=_auth(token),
    )).json()
    get_extension_runtime().capabilities.provide("fake-services", ServiceCatalog, _Services())
    await _create(
        client, token, name="NAS", url="http://192.168.2.20:5000", icon="hard-drive", color="#10b981", group="Speicher",
        open_in_new_tab=False, host_id=host["id"],
    )
    await _create(client, token, name="Router", url="http://192.168.2.1", group="Netzwerk")

    body = (await client.get("/api/v1/overview", headers=_auth(token))).json()

    # Die bisherigen Felder bleiben unveraendert: nur erkannte Dienste, keine eigenen Kacheln darin.
    assert [s["name"] for s in body["services"]] == ["grafana", "redis"]
    assert body["services_running"] == 1
    assert {"services", "services_running", "backups", "pending_actions", "unread_notifications", "attention", "errors",
            "generated_at"} <= set(body)

    apps = body["apps"]
    assert [(a["name"], a["source"]) for a in apps] == [
        ("NAS", "custom"), ("Router", "custom"), ("grafana", "detected"), ("redis", "detected"),
    ], "eigene zuerst (nach Position), danach die erkannten Dienste wie in services"
    nas, router, grafana, redis = apps
    assert nas["id"] and nas["url"] == "http://192.168.2.20:5000" and nas["icon"] == "hard-drive"
    assert (nas["color"], nas["group"], nas["open_in_new_tab"], nas["host_id"], nas["host"]) == ("#10b981", "Speicher", False, host["id"], "Mein NAS")
    assert nas["state"] is None and nas["tone"] is None and nas["image"] is None
    assert router["host"] is None and router["open_in_new_tab"] is True
    assert (grafana["id"], grafana["url"], grafana["state"], grafana["host"], grafana["image"]) == (
        "h1:grafana", "http://10.0.0.5:3000", "running", "docker-host", "grafana/grafana",
    )
    assert grafana["icon"] is None and grafana["group"] is None and grafana["open_in_new_tab"] is True
    assert redis["url"] is None


@pytest.mark.asyncio
async def test_overview_shows_custom_apps_without_any_service_provider(client):
    """Wer keine Service-Matrix hat, bekommt trotzdem seine eigenen Kacheln."""
    token = await _bootstrap_owner(client)
    await _create(client, token, name="Router")
    body = (await client.get("/api/v1/overview", headers=_auth(token))).json()
    assert body["services"] == [] and body["services_running"] == 0
    assert [(a["name"], a["source"]) for a in body["apps"]] == [("Router", "custom")]
    assert body["errors"] == []


@pytest.mark.asyncio
async def test_overview_without_any_apps_has_an_empty_list(client):
    token = await _bootstrap_owner(client)
    body = (await client.get("/api/v1/overview", headers=_auth(token))).json()
    assert body["apps"] == []


@pytest.mark.asyncio
async def test_custom_apps_are_fresh_even_though_services_are_cached(client):
    token = await _bootstrap_owner(client)
    services = _Services()
    get_extension_runtime().capabilities.provide("fake-services", ServiceCatalog, services)
    first = (await client.get("/api/v1/overview", headers=_auth(token))).json()
    assert [a["name"] for a in first["apps"]] == ["grafana", "redis"]

    created = (await _create(client, token, name="Neu")).json()
    second = (await client.get("/api/v1/overview", headers=_auth(token))).json()
    assert [a["name"] for a in second["apps"]] == ["Neu", "grafana", "redis"]
    assert services.calls == 1, "die erkannten Dienste kommen weiter aus dem 30-s-Zwischenspeicher"

    await client.delete(f"/api/v1/apps/{created['id']}", headers=_auth(token))
    third = (await client.get("/api/v1/overview", headers=_auth(token))).json()
    assert [a["name"] for a in third["apps"]] == ["grafana", "redis"]


@pytest.mark.asyncio
async def test_overview_does_not_pass_on_a_stored_url_that_is_not_http(client, db_session):
    """Selbst wenn jemand die Datenbank von Hand veraendert hat: kein javascript:-Link in der Uebersicht."""
    token = await _bootstrap_owner(client)
    db_session.add(CustomApp(name="Böse", url="javascript:alert(1)", sort_order=0))
    db_session.add(CustomApp(name="Gut", url="http://192.168.2.1", sort_order=1))
    await db_session.commit()
    apps = (await client.get("/api/v1/overview", headers=_auth(token))).json()["apps"]
    assert {a["name"]: a["url"] for a in apps} == {"Böse": None, "Gut": "http://192.168.2.1"}
    listed = (await client.get("/api/v1/apps", headers=_auth(token))).json()
    assert {a["name"]: a["url"] for a in listed} == {"Böse": None, "Gut": "http://192.168.2.1"}


@pytest.mark.asyncio
async def test_viewer_sees_custom_apps_in_the_overview(client, db_session):
    owner = await _bootstrap_owner(client)
    await _create(client, owner, name="Router")
    viewer = await _user_token(client, db_session, "betrachter", role="viewer")
    body = (await client.get("/api/v1/overview", headers=_auth(viewer))).json()
    assert [a["name"] for a in body["apps"]] == ["Router"]


@pytest.mark.asyncio
async def test_overview_still_needs_hosts_read(client, db_session):
    await _bootstrap_owner(client)
    token = await _user_token(client, db_session, "nur-meldungen", permissions=("notifications.read",))
    assert (await client.get("/api/v1/overview", headers=_auth(token))).status_code == 403


# --- Der Server ruft nie selbst eine Adresse ab -----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_server_never_fetches_the_addresses(client, monkeypatch):
    """Keine Statusabfrage in diesem Paket (kein SSRF): beim Anlegen, Lesen, Aendern und Loeschen wird
    weder ein Name aufgeloest noch eine Verbindung geoeffnet. (Der Test-Client spricht ueber ASGI, nicht ueber Sockets.)"""
    import socket

    def boom(*args, **kwargs):
        raise AssertionError("Eine eigene App-Adresse darf der Server nie selbst aufrufen.")

    token = await _bootstrap_owner(client)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)

    created = await _create(client, token, url="http://nicht-aufloesbar.invalid/")
    assert created.status_code == 201, created.text
    assert (await client.get("/api/v1/apps", headers=_auth(token))).status_code == 200
    assert (await client.get("/api/v1/overview", headers=_auth(token))).status_code == 200
    assert (await client.patch(f"/api/v1/apps/{created.json()['id']}", json={"url": "https://auch-nicht.invalid"}, headers=_auth(token))).status_code == 200
    assert (await client.delete(f"/api/v1/apps/{created.json()['id']}", headers=_auth(token))).status_code == 204
