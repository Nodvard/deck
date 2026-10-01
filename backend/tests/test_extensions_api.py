"""Abnahmetest fuer den Extension-Host: `hello-world` aktivieren/deaktivieren ueber die API
aendert Navigation (`/pages`), Widget-Katalog (`/widgets`) und Routen
(`/api/v1/ext/hello-world/...`) zur Laufzeit, ohne Neustart.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nodvard_deck.services import extensions as extensions_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _create_user_with_role(db_session, *, username, password, role):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password(password), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.flush()
    return user


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _discover_real_hello_world(db_session, test_settings) -> None:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)


@pytest.mark.asyncio
async def test_capabilities_is_public_and_starts_without_extensions(client):
    r = await client.get("/api/v1/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["extensions"] == []
    assert "list" in body["view_types"]


@pytest.mark.asyncio
async def test_capabilities_reports_demo_mode_feature_flag(client, test_settings):
    """LATTICE_DEMO_MODE ist verdrahtet: `feature_flags` war seit seiner Einfuehrung immer `{}` -- kein
    Aufrufer brauchte es. `demo_mode` ist der erste echte Verbraucher."""
    off = await client.get("/api/v1/capabilities")
    assert off.json()["feature_flags"] == {"demo_mode": False}

    test_settings.demo_mode = True
    on = await client.get("/api/v1/capabilities")
    assert on.json()["feature_flags"] == {"demo_mode": True}


@pytest.mark.asyncio
async def test_full_enable_disable_cycle_changes_nav_widgets_and_routes_without_restart(
    client, db_session, test_settings
):
    token = await _bootstrap_owner(client)
    await _discover_real_hello_world(db_session, test_settings)

    listed = await client.get("/api/v1/extensions", headers=_auth_header(token))
    assert listed.status_code == 200
    by_id = {e["id"]: e for e in listed.json()}
    assert by_id["hello-world"]["state"] == "disabled"

    # VOR dem Aktivieren: weder Seite noch Widget noch eigene Route.
    pages_before = await client.get("/api/v1/pages", headers=_auth_header(token))
    widgets_before = await client.get("/api/v1/widgets", headers=_auth_header(token))
    assert pages_before.json() == []
    assert widgets_before.json() == []
    route_before = await client.get("/api/v1/ext/hello-world/widgets/hello")
    assert route_before.status_code == 404

    enabled = await client.post("/api/v1/extensions/hello-world/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["state"] == "enabled"

    # DANACH, OHNE NEUSTART: Navigation, Widget-Katalog und Route aendern sich.
    pages_after = await client.get("/api/v1/pages", headers=_auth_header(token))
    widgets_after = await client.get("/api/v1/widgets", headers=_auth_header(token))
    assert [p["id"] for p in pages_after.json()] == ["hello"]
    assert [w["id"] for w in widgets_after.json()] == ["hello"]

    route_after = await client.get("/api/v1/ext/hello-world/widgets/hello")
    assert route_after.status_code == 200
    assert route_after.json()["data"][0]["title"] == "Hallo Welt"

    caps = await client.get("/api/v1/capabilities")
    assert any(e["id"] == "hello-world" for e in caps.json()["extensions"])

    # Deaktivieren macht ALLES davon wieder rueckgaengig -- immer noch ohne Neustart.
    disabled = await client.post("/api/v1/extensions/hello-world/disable", headers=_auth_header(token))
    assert disabled.status_code == 200
    assert disabled.json()["state"] == "disabled"

    assert (await client.get("/api/v1/pages", headers=_auth_header(token))).json() == []
    assert (await client.get("/api/v1/widgets", headers=_auth_header(token))).json() == []
    assert (await client.get("/api/v1/ext/hello-world/widgets/hello")).status_code == 404
    caps_after = await client.get("/api/v1/capabilities")
    assert caps_after.json()["extensions"] == []


@pytest.mark.asyncio
async def test_include_router_permission_param_protects_route_default_stays_open(
    client, db_session, test_settings
):
    """`ctx.api.include_router(..., permission=...)` ist der Fix fuer eine live
    gefundene Luecke -- vorher pruefte KEINE Extension-Route je eine Berechtigung,
    obwohl der SDK-Docstring das Gegenteil behauptete. `/widgets/hello`
    bleibt bewusst der Default-Fall (kein `permission=`, weiterhin oeffentlich);
    `/protected-widget` ist die neue, geschuetzte Route."""
    await _discover_real_hello_world(db_session, test_settings)
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/extensions/hello-world/enable", headers=_auth_header(token))

    unauthenticated = await client.get("/api/v1/ext/hello-world/protected-widget")
    assert unauthenticated.status_code == 401

    authenticated = await client.get(
        "/api/v1/ext/hello-world/protected-widget", headers=_auth_header(token)
    )
    assert authenticated.status_code == 200
    assert authenticated.json() == {"ok": True}

    # Der Default (kein permission=) bleibt unveraendert oeffentlich -- keine
    # rueckwirkende Verschaerfung bestehender Extension-Routen.
    still_open = await client.get("/api/v1/ext/hello-world/widgets/hello")
    assert still_open.status_code == 200


@pytest.mark.asyncio
async def test_notify_test_endpoint_creates_a_real_notification(client, db_session, test_settings):
    """`ctx.notify.send()` ueber einen echten HTTP-Request -- das
    Notification-Center selbst ist absichtlich nur lesend ueber die API erreichbar."""
    token = await _bootstrap_owner(client)
    await _discover_real_hello_world(db_session, test_settings)
    await client.post("/api/v1/extensions/hello-world/enable", headers=_auth_header(token))

    r = await client.post("/api/v1/ext/hello-world/notify-test")
    assert r.status_code == 200, r.text

    listed = await client.get("/api/v1/notifications", headers=_auth_header(token))
    assert any(n["title"] == "Testmeldung von hello-world" for n in listed.json())


@pytest.mark.asyncio
async def test_vault_use_isolation_survives_a_real_extension_request_failure(
    client, db_session, test_settings
):
    """Die Auflage aus Commit 1ee1dbe, jetzt gegen den echten Aufrufer: der
    Extension-Host ruft ctx.vault_use() ueber einen echten HTTP-Request auf, der
    danach absichtlich scheitert (500) -- der secret.used-Audit-Eintrag muss trotzdem
    stehen bleiben."""
    token = await _bootstrap_owner(client)
    await _discover_real_hello_world(db_session, test_settings)
    await client.post("/api/v1/extensions/hello-world/enable", headers=_auth_header(token))

    r = await client.post("/api/v1/ext/hello-world/vault-use-and-fail")
    assert r.status_code == 500

    audit = await client.get(
        "/api/v1/audit", params={"action": "secret.used"}, headers=_auth_header(token)
    )
    assert audit.status_code == 200
    entries = audit.json()
    assert len(entries) == 1
    assert entries[0]["actor_type"] == "extension"
    assert entries[0]["actor_id"] == "hello-world"
    assert entries[0]["detail"] == {"label": "hello-world-demo"}


@pytest.mark.asyncio
async def test_frontend_bundle_is_served_regardless_of_enabled_state(client, db_session, test_settings):
    """Das ESM-Bundle ist wie das Kern-Frontend selbst oeffentlich und haengt
    NICHT vom Ladezustand ab (docs/02 §5: der Kern liefert es unter
    `/api/v1/extensions/<id>/frontend/index.js`, ladbar per `import()` bevor die
    Seite ueberhaupt weiss, ob die Extension aktiv ist)."""
    await _discover_real_hello_world(db_session, test_settings)

    before_enable = await client.get("/api/v1/extensions/hello-world/frontend/index.js")
    assert before_enable.status_code == 200, before_enable.text
    assert before_enable.headers["content-type"].startswith("application/javascript")
    assert b"HelloPage" in before_enable.content

    token = await _bootstrap_owner(client)
    await client.post("/api/v1/extensions/hello-world/enable", headers=_auth_header(token))
    after_enable = await client.get("/api/v1/extensions/hello-world/frontend/index.js")
    assert after_enable.status_code == 200
    assert after_enable.content == before_enable.content


@pytest.mark.asyncio
async def test_frontend_bundle_must_be_revalidated_and_answers_304_when_unchanged(client, db_session, test_settings):
    """Live gefunden: nach einem Redeploy zeigte der Browser weiter das alte Bundle
    (fester URL ohne Inhaltshash, kein `Cache-Control` -> heuristisches Caching).
    `no-cache` zwingt zur Revalidierung; per ETag bleibt die ein billiges 304."""
    await _discover_real_hello_world(db_session, test_settings)

    first = await client.get("/api/v1/extensions/hello-world/frontend/index.js")
    assert first.headers["cache-control"] == "no-cache"
    etag = first.headers["etag"]

    revalidated = await client.get(
        "/api/v1/extensions/hello-world/frontend/index.js", headers={"If-None-Match": etag}
    )
    assert revalidated.status_code == 304


@pytest.mark.asyncio
async def test_frontend_bundle_404_for_unknown_extension_and_missing_manifest_field(
    client, db_session, test_settings
):
    missing_ext = await client.get("/api/v1/extensions/does-not-exist/frontend/index.js")
    assert missing_ext.status_code == 404

    # ntfy hat kein `frontend` in seiner extension.toml.
    await _discover_real_hello_world(db_session, test_settings)
    no_frontend_field = await client.get("/api/v1/extensions/ntfy/frontend/index.js")
    assert no_frontend_field.status_code == 404


@pytest.mark.asyncio
async def test_enable_requires_extensions_manage_permission(client, db_session, test_settings):
    await _create_user_with_role(db_session, username="viewer1", password="whatever123", role="viewer")
    await _discover_real_hello_world(db_session, test_settings)

    login = await client.post(
        "/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"}
    )
    token = login.json()["access_token"]

    r = await client.post("/api/v1/extensions/hello-world/enable", headers=_auth_header(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_enable_unknown_extension_returns_404(client, db_session, test_settings):
    token = await _bootstrap_owner(client)
    r = await client.post("/api/v1/extensions/does-not-exist/enable", headers=_auth_header(token))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_extensions_requires_authentication(client):
    r = await client.get("/api/v1/extensions")
    assert r.status_code == 401
