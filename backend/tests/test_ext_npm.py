"""Nginx Proxy Manager durch den echten Kern: Laden, Einstellungen, Routen, Kacheln, Aktionen über das Gate und der
tägliche Zertifikats-Wächter. Nginx Proxy Manager ist ein Nachbau hinter `httpx.MockTransport`."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from nodvard_deck.core import vault
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import Action, AuditEntry, ExtensionRecord, Secret
from nodvard_deck.services import extensions as extensions_service
from sqlalchemy import select

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
NPM_URL = "http://npm.test:81"
NPM_PASSWORD = "npm-geheim-456"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content) if request.content else {}


def _expiry(days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class FakeServers:
    """Nginx Proxy Manager im Kleinen."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.down: set[str] = set()
        self.npm_tokens: set[str] = set()
        self.npm_calls: list[str] = []
        self.hosts = [
            {"id": 1, "domain_names": ["cloud.home.example"], "forward_scheme": "http", "forward_host": "192.168.1.85", "forward_port": 8080,
             "certificate_id": 11, "ssl_forced": True, "enabled": True, "meta": {"nginx_online": True}},
            {"id": 2, "domain_names": ["nas.home.example", "fotos.home.example"], "forward_scheme": "https", "forward_host": "192.168.1.10",
             "forward_port": 5001, "certificate_id": 12, "ssl_forced": True, "enabled": True, "meta": {}},
            {"id": 3, "domain_names": ["alt.home.example"], "forward_scheme": "http", "forward_host": "192.168.1.20", "forward_port": 80,
             "certificate_id": 13, "ssl_forced": False, "enabled": False, "meta": {}},
        ]
        self.certs = [
            {"id": 11, "provider": "letsencrypt", "nice_name": "cloud.home.example", "domain_names": ["cloud.home.example"], "expires_on": _expiry(60)},
            {"id": 12, "provider": "letsencrypt", "nice_name": "nas.home.example", "domain_names": ["nas.home.example", "fotos.home.example"], "expires_on": _expiry(5.5)},
            {"id": 13, "provider": "other", "nice_name": "Altes Zertifikat", "domain_names": ["alt.home.example"], "expires_on": _expiry(-3.5)},
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host in self.down:
            raise httpx.ConnectError("[Errno 111] Connection refused")
        if request.url.host == "npm.test":
            return self._npm(request)
        return httpx.Response(404)

    def _npm(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/tokens":
            body = _body(request)
            if (body.get("identity"), body.get("secret")) != ("admin@home.example", NPM_PASSWORD):
                return httpx.Response(401, json={"error": {"code": 401, "message": "Invalid email or password"}})
            token = f"jwt-{len(self.npm_tokens) + 1}"
            self.npm_tokens.add(token)
            return httpx.Response(200, json={"token": token, "expires": _expiry(1)})
        if request.headers.get("Authorization", "")[7:] not in self.npm_tokens:
            return httpx.Response(401, json={"error": {"code": 401, "message": "Unauthorized"}})
        if path == "/api/nginx/proxy-hosts":
            return httpx.Response(200, json=self.hosts)
        if path == "/api/nginx/certificates":
            return httpx.Response(200, json=self.certs)
        for host in self.hosts:
            for verb, value in (("enable", True), ("disable", False)):
                if path == f"/api/nginx/proxy-hosts/{host['id']}/{verb}":
                    self.npm_calls.append(f"{verb} {host['id']}")
                    if host["enabled"] == value:
                        return httpx.Response(400, json={"error": {"code": 400, "message": f"Host is already {verb}d"}})
                    host["enabled"] = value
                    return httpx.Response(200, json=True)
        return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})


async def _login(client, username="owner1", password="correct-horse-battery") -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _enable(client, db_session, test_settings) -> tuple[dict, FakeServers]:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    headers = await _login(client)
    enabled = await client.post("/api/v1/extensions/npm/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text

    fake = FakeServers()
    http = get_extension_runtime().loaded["npm"].ctx.http
    mocked = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    http._client = mocked
    http._insecure_client = mocked
    return headers, fake


async def _configure(client, headers) -> None:
    values = {"url": NPM_URL, "identity": "admin@home.example"}
    saved = await client.put("/api/v1/extensions/npm/settings", json={"current_password": "correct-horse-battery", "values": values}, headers=headers)
    assert saved.status_code == 200, saved.text
    res = await client.put(
        "/api/v1/extensions/npm/secrets", json={"current_password": "correct-horse-battery", "label": "network-npm-password", "value": NPM_PASSWORD}, headers=headers,
    )
    assert res.status_code == 204, res.text


async def _replace_unannounced(db_session, test_settings, value: str) -> None:
    secret = (await db_session.execute(select(Secret).where(Secret.label == "network-npm-password"))).scalar_one()
    await vault.replace_secret_value(db_session, vault.load_keyring(test_settings), secret.id, value)
    await db_session.commit()


async def _approve(client, headers, action_id: str) -> dict:
    res = await client.post(f"/api/v1/actions/{action_id}/approve", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------------
# Laden, Katalog, "nicht eingerichtet"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_npm_is_discovered_disabled_and_loads_cleanly(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    record = await db_session.get(ExtensionRecord, "npm")
    assert record is not None and record.state == "disabled"
    assert record.manifest["stage"] == "beta"

    headers, _ = await _enable(client, db_session, test_settings)
    assert record.state == "enabled", record.last_error
    assert "npm" in get_extension_runtime().loaded

    pages = (await client.get("/api/v1/pages", headers=headers)).json()
    page = next(p for p in pages if p["ext_id"] == "npm")
    assert (page["path"], page["component"], page["title"]) == ("/npm", "NpmPage", "Nginx Proxy Manager")
    widgets = {w["id"]: w for w in (await client.get("/api/v1/widgets", headers=headers)).json() if w["ext_id"] == "npm"}
    assert set(widgets) == {"certificates"}
    assert widgets["certificates"]["view"]["item"]["badge"] == {"text": "{{ label }}", "tone": "{{ tone }}"}

    bundle = await client.get("/api/v1/extensions/npm/frontend/index.js")
    assert bundle.status_code == 200
    assert "NpmPage" in bundle.text


@pytest.mark.asyncio
async def test_nothing_configured_is_reported_cleanly_everywhere(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)

    npm = (await client.get("/api/v1/ext/npm/summary", headers=headers)).json()
    assert npm["state"] == "not_configured" and npm["hosts"] == []
    certs = (await client.get("/api/v1/ext/npm/widgets/certificates", headers=headers)).json()
    assert certs["data"][0]["title"] == "Nginx Proxy Manager nicht eingerichtet"

    enable = await client.post("/api/v1/ext/npm/hosts/1/enable", headers=headers)
    assert enable.status_code == 409

    health = await get_extension_runtime().loaded["npm"].instance.health(get_extension_runtime().loaded["npm"].ctx)
    assert health.healthy is False and "noch nicht eingerichtet" in health.message
    assert fake.requests == [], "ohne Einstellungen wird nichts angefragt"
    assert (await db_session.execute(select(Action))).scalars().all() == []


@pytest.mark.asyncio
async def test_npm_with_address_but_without_password_says_what_is_missing(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    saved = await client.put(
        "/api/v1/extensions/npm/settings",
        json={"current_password": "correct-horse-battery", "values": {"url": NPM_URL, "identity": "admin@home.example"}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    npm = (await client.get("/api/v1/ext/npm/summary", headers=headers)).json()
    assert npm["state"] == "not_configured"
    assert "fehlt noch das Passwort" in npm["message"]
    assert fake.requests == []


@pytest.mark.asyncio
async def test_widget_meta_says_not_configured_only_when_nothing_usable_is_entered(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)

    async def widget() -> dict:
        res = await client.get("/api/v1/ext/npm/widgets/certificates", headers=headers)
        assert res.status_code == 200, res.text
        return res.json()

    async def save(values: dict) -> None:
        res = await client.put("/api/v1/extensions/npm/settings", json={"current_password": "correct-horse-battery", "values": values}, headers=headers)
        assert res.status_code == 200, res.text

    # Nichts eingetragen: die Übersicht darf die Kachel ausblenden; die Textzeile bleibt für ältere Oberflächen.
    body = await widget()
    assert body["meta"] == {"state": "not_configured"} and body["data"][0]["label"] == "nicht eingerichtet"

    # Eine eingetragene, aber unbrauchbare Adresse ist ein Fehler und bleibt sichtbar.
    await save({"url": "ftp://npm.test", "identity": "admin@home.example"})
    body = await widget()
    assert body["meta"] == {"state": "error"} and "ist ungültig" in body["data"][0]["subtitle"]

    # Eine gültige Adresse gilt als eingerichtet, auch wenn der Nginx Proxy Manager nicht antwortet.
    await _configure(client, headers)
    fake.down.add("npm.test")
    assert (await widget())["meta"] == {"state": "unreachable"}
    fake.down.clear()
    assert (await widget())["meta"] == {"state": "ok"}


@pytest.mark.asyncio
async def test_routes_reject_anonymous_and_viewers_cannot_propose(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    routes = [
        ("GET", "/api/v1/ext/npm/summary"),
        ("GET", "/api/v1/ext/npm/widgets/certificates"),
        ("POST", "/api/v1/ext/npm/hosts/1/enable"),
        ("POST", "/api/v1/ext/npm/hosts/1/disable"),
    ]
    results = {f"{m} {p}": (await client.request(m, p, json={"minutes": 5})).status_code for m, p in routes}
    assert results == {key: 401 for key in results}

    roles = {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers=headers)).json()}
    created = await client.post(
        "/api/v1/users", json={"username": "gast", "password": "gast-passwort-1", "role_ids": [roles["viewer"]]}, headers=headers,
    )
    assert created.status_code == 201, created.text
    login = await client.post("/api/v1/auth/login", json={"username": "gast", "password": "gast-passwort-1"})
    viewer = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get("/api/v1/ext/npm/summary", headers=viewer)).status_code == 200
    assert (await client.post("/api/v1/ext/npm/hosts/1/disable", headers=viewer)).status_code == 403
    assert (await db_session.execute(select(Action))).scalars().all() == []
    assert fake.npm_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_type", "payload", "error"),
    [
        ("network.npm_host_disable", {"host_id": "1; rm -rf /"}, "ganze Zahl"),
        ("network.npm_host_disable", {"host_id": 0}, "unbekannte Nummer"),
        ("network.npm_host_enable", {}, "ganze Zahl"),
        ("network.npm_host_enable", {"host_id": 1.0}, "ganze Zahl"),
    ],
)
async def test_executor_rejects_forged_payloads(client, db_session, test_settings, action_type, payload, error):
    """Der Payload liegt zwischen Vorschlag und Ausfuehrung in der DB -- der Executor
    prueft ihn deshalb selbst noch einmal, statt ihm zu trauen."""
    from nodvard_sdk import Actor, Risk
    from nodvard_sdk.actions import ActionRequest

    from nodvard_deck.core.known_actors import known_while

    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    ctx = get_extension_runtime().loaded["npm"].ctx
    with known_while([("user", "x")]):
        decision = await ctx.actions.propose(ActionRequest(
            action_type=action_type, payload=payload, risk=Risk.LOW, proposed_by=Actor.user("x", "x"), reason="Test",
        ))
    approved = await _approve(client, headers, decision.action_id)
    assert approved["status"] == "failed", approved
    assert error in approved["result"]["error"]
    assert fake.npm_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("location", "sentence"),
    [
        ("https://angreifer.example/login", "leitet auf eine andere Adresse um (HTTP 302)."),
        # Ungueltiger Punycode-Name: httpx scheitert beim Zusammenbauen der Weiterleitung mit einem
        # `ValueError` (idna), dessen Text Teile des fremden Namens nennt.
        ("http://xn--angreifer-ey9f.example/login", "leitet auf eine andere Adresse um."),
    ],
)
async def test_redirects_are_clean_states_and_never_show_the_foreign_address(
    client, db_session, test_settings, caplog, location, sentence
):
    caplog.set_level(logging.INFO)
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    seen: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": location})

    loaded = get_extension_runtime().loaded["npm"]
    http = loaded.ctx.http
    http._client = http._insecure_client = httpx.AsyncClient(transport=httpx.MockTransport(redirect))

    npm_res = await client.get("/api/v1/ext/npm/summary", headers=headers)
    assert npm_res.status_code == 200, npm_res.text
    npm = npm_res.json()
    assert npm["state"] == "unreachable"
    assert npm["message"].startswith(f"Nginx Proxy Manager {sentence}")
    widget = await client.get("/api/v1/ext/npm/widgets/certificates", headers=headers)
    assert widget.status_code == 200 and "leitet auf eine andere Adresse um" in widget.text, widget.text
    proposed = await client.post("/api/v1/ext/npm/hosts/1/disable", headers=headers)
    assert proposed.status_code == 502 and "leitet auf eine andere Adresse um" in proposed.json()["detail"]
    health = await loaded.instance.health(loaded.ctx)
    assert health.healthy is False
    watch = await get_extension_runtime().scheduler.get("npm", "cert-watch")()
    assert "leitet auf eine andere Adresse um" in watch["skipped"]
    everything = json.dumps([npm, widget.text, proposed.text, health.message, watch]) + caplog.text
    assert "angreifer" not in everything.lower()
    assert "Traceback" not in caplog.text
    assert all(r.url.host != "angreifer.example" for r in seen)


# ---------------------------------------------------------------------------
# Nginx Proxy Manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_npm_hosts_certificates_and_widget(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)

    body = (await client.get("/api/v1/ext/npm/summary", headers=headers)).json()
    assert body["state"] == "ok", body
    assert body["summary"] == {"hosts": 3, "hosts_enabled": 2, "certificates": 3, "certificates_warn": 1, "certificates_expired": 1}
    assert [h["domains"][0] for h in body["hosts"]] == ["alt.home.example", "cloud.home.example", "nas.home.example"]
    nas = next(h for h in body["hosts"] if h["id"] == 2)
    assert nas["target"] == "https://192.168.1.10:5001"
    assert nas["certificate"]["status"] == "warn"
    assert nas["certificate"]["days_text"] == "noch 5 Tage"
    assert [c["status"] for c in body["certificates"]] == ["expired", "warn", "ok"], "dringendste zuerst"

    rows = (await client.get("/api/v1/ext/npm/widgets/certificates", headers=headers)).json()["data"]
    assert [(r["title"], r["label"], r["tone"]) for r in rows] == [
        ("Altes Zertifikat", "abgelaufen", "danger"),
        ("nas.home.example", "läuft bald ab", "warn"),
    ]
    assert "abgelaufen (seit 3 Tagen abgelaufen)" in rows[0]["subtitle"]

    # Alles erneuert -> eine gruene Zeile statt einer leeren Kachel.
    for cert in fake.certs:
        cert["expires_on"] = _expiry(80)
    rows = (await client.get("/api/v1/ext/npm/widgets/certificates", headers=headers)).json()["data"]
    assert len(rows) == 1 and rows[0]["tone"] == "good" and rows[0]["title"].startswith("Alle Zertifikate gültig")
    assert sum(1 for r in fake.requests if r.url.path == "/api/tokens") == 1


@pytest.mark.asyncio
async def test_npm_disable_host_through_the_gate(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)

    proposed = await client.post("/api/v1/ext/npm/hosts/2/disable", headers=headers)
    assert proposed.status_code == 200, proposed.text
    body = proposed.json()
    assert (body["status"], body["risk"]) == ("proposed", "medium")
    assert fake.npm_calls == []

    approved = await _approve(client, headers, body["action_id"])
    assert approved["status"] == "succeeded", approved
    assert fake.npm_calls == ["disable 2"]
    assert approved["result"]["output"] == "„nas.home.example, fotos.home.example“ ist jetzt ausgeschaltet."
    assert approved["payload"] == {"host_id": 2}
    assert "nas.home.example" in approved["reason"]

    # Schon aus -> kein zweiter Vorschlag; unbekannter Host -> 404.
    again = await client.post("/api/v1/ext/npm/hosts/2/disable", headers=headers)
    assert again.status_code == 409 and "schon ausgeschaltet" in again.json()["detail"]
    assert (await client.post("/api/v1/ext/npm/hosts/99/enable", headers=headers)).status_code == 404
    assert (await client.post("/api/v1/ext/npm/hosts/0/enable", headers=headers)).status_code == 422

    enable = (await client.post("/api/v1/ext/npm/hosts/2/enable", headers=headers)).json()
    approved = await _approve(client, headers, enable["action_id"])
    assert approved["status"] == "succeeded"
    assert fake.npm_calls == ["disable 2", "enable 2"]


@pytest.mark.asyncio
async def test_connection_test_checks_the_password_now_in_the_vault(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    assert (await client.get("/api/v1/ext/npm/summary", headers=headers)).json()["state"] == "ok"
    await _replace_unannounced(db_session, test_settings, "falsch")
    assert fake.npm_tokens == {"jwt-1"}, "das alte Token gilt bei NPM noch"

    tested = (await client.post("/api/v1/extensions/npm/test", headers=headers)).json()
    assert tested["ok"] is False and "Passwort" in tested["message"], tested

    await _replace_unannounced(db_session, test_settings, NPM_PASSWORD)
    tested = (await client.post("/api/v1/extensions/npm/test", headers=headers)).json()
    assert tested["ok"] is True, tested
    assert sum(1 for r in fake.requests if r.url.path == "/api/tokens") == 3


@pytest.mark.asyncio
async def test_a_new_password_in_the_vault_drops_the_old_token(client, db_session, test_settings):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    assert (await client.get("/api/v1/ext/npm/summary", headers=headers)).json()["state"] == "ok"
    body = {"current_password": "correct-horse-battery", "label": "network-npm-password", "value": "falsch"}
    assert (await client.put("/api/v1/extensions/npm/secrets", json=body, headers=headers)).status_code == 204

    npm = (await client.get("/api/v1/ext/npm/summary", headers=headers)).json()
    assert npm["state"] != "ok" and "Passwort" in npm["message"], npm


@pytest.mark.asyncio
async def test_npm_unreachable_is_a_clean_state_and_blocks_proposals(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.down.add("npm.test")

    body = (await client.get("/api/v1/ext/npm/summary", headers=headers)).json()
    assert body["state"] == "unreachable" and body["hosts"] == []
    rows = (await client.get("/api/v1/ext/npm/widgets/certificates", headers=headers)).json()["data"]
    assert rows[0]["title"] == "Nginx Proxy Manager nicht erreichbar" and rows[0]["tone"] == "danger"
    res = await client.post("/api/v1/ext/npm/hosts/1/disable", headers=headers)
    assert res.status_code == 502
    assert "nicht erreichbar" in res.json()["detail"]

    health = await get_extension_runtime().loaded["npm"].instance.health(get_extension_runtime().loaded["npm"].ctx)
    assert health.healthy is False and "Nginx Proxy Manager" in health.message


@pytest.mark.asyncio
async def test_npm_fehler_ohne_text_liefert_trotzdem_einen_grund(client, db_session, test_settings, monkeypatch):
    """Ein 502 mit leerem `detail` sieht fuer die Seite wie eine Proxy-Fehlerseite aus
    ("Server gerade nicht erreichbar"). Deshalb bekommt es mindestens den Namen des Fehlers."""
    headers, _fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    npm = sys.modules["nodvard_deck_ext_npm.client"]

    async def kaputt(self, certificates=None):
        raise npm.NpmError("")

    monkeypatch.setattr(npm.NpmClient, "proxy_hosts", kaputt)
    res = await client.post("/api/v1/ext/npm/hosts/1/disable", headers=headers)
    assert res.status_code == 502
    assert res.json()["detail"] == "NpmError"


# ---------------------------------------------------------------------------
# Zertifikats-Waechter
# ---------------------------------------------------------------------------


async def _cert_notifications(db_session) -> list[tuple[str, str]]:
    from nodvard_deck.models import Notification as NotificationRow

    rows = (await db_session.execute(
        select(NotificationRow).where(NotificationRow.source_ext_id == "npm").order_by(NotificationRow.ts)
    )).scalars().all()
    return [(r.severity, r.title) for r in rows]


@pytest.mark.asyncio
async def test_cert_watch_notifies_once_per_certificate_and_day(client, db_session, test_settings):
    headers, _fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    loaded = get_extension_runtime().loaded["npm"]
    handler = get_extension_runtime().scheduler.get("npm", "cert-watch")
    assert handler is not None

    assert (await handler())["notified"] == 2
    assert (await handler())["notified"] == 0, "am selben Tag keine zweite Meldung"
    notes = await _cert_notifications(db_session)
    assert sorted(notes) == [("critical", "Zertifikat abgelaufen: Altes Zertifikat"), ("warning", "Zertifikat läuft bald ab: nas.home.example")]

    certwatch = sys.modules["nodvard_deck_ext_npm.certwatch"]
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    result = await certwatch.run_cert_watch(loaded.ctx, loaded.instance._service, now=tomorrow)
    assert result["notified"] == 2, "am naechsten Tag wieder, solange nicht erneuert"

    # Abschaltbar in den Einstellungen.
    saved = await client.put(
        "/api/v1/extensions/npm/settings",
        json={"current_password": "correct-horse-battery", "values": {"url": NPM_URL, "identity": "admin@home.example", "notify_expiring": False}}, headers=headers,
    )
    assert saved.status_code == 200
    assert (await certwatch.run_cert_watch(loaded.ctx, loaded.instance._service, now=tomorrow + timedelta(days=1)))["notified"] == 0


@pytest.mark.asyncio
async def test_cert_watch_is_quiet_when_not_configured(client, db_session, test_settings):
    await _enable(client, db_session, test_settings)
    handler = get_extension_runtime().scheduler.get("npm", "cert-watch")
    assert (await handler())["notified"] == 0
    assert await _cert_notifications(db_session) == []


# Stand vor dem Update (0.8): Der Nginx Proxy Manager steckt im Modul „Netzwerk“, das beim Einschalten diese Rechte bekam.
OLD_NETWORK_GRANTED = [
    "net.outbound", "net.outbound.insecure_tls", "hosts.execute", "secrets.read:network-*", "schedule.register",
    "notify.send",
]
OLD_NETWORK = {"npm": {"url": NPM_URL, "identity": "admin@home.example", "notify_expiring": True}}


async def _installation_before(db_session, test_settings, *, state: str = "enabled", settings: dict | None = None) -> str:
    from nodvard_deck.core import vault
    from nodvard_deck.models import DashboardLayout

    db_session.add(ExtensionRecord(
        id="network", version="0.1.0", api_version="0.1.0", state=state, source="bundled",
        settings=OLD_NETWORK if settings is None else settings, manifest={"id": "network", "name": "Netzwerk"},
        granted_permissions=OLD_NETWORK_GRANTED,
    ))
    db_session.add(DashboardLayout(user_id="u1", name="Standard", is_default=True, items=[
        {"ext_id": "network", "widget_id": "certificates", "x": 2, "y": 0, "w": 2, "h": 2, "config": {}},
    ]))
    await vault.create_secret(
        db_session, vault.load_keyring(test_settings), label="network-npm-password", kind="generic",
        plaintext=NPM_PASSWORD, owner_ext_id="network",
    )
    proposal = Action(
        ext_id="network", action_type="network.npm_host_disable", payload={"host_id": 2}, risk="medium", status="proposed",
        proposed_by_type="user", proposed_by_id="x", reason="Vor dem Update vorgeschlagen",
    )
    db_session.add(proposal)
    await db_session.commit()
    return proposal.id


async def _boot(db_session, test_settings) -> None:
    from nodvard_deck.main import app

    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    await extensions_service.load_enabled_from_registry(app, db_session, test_settings)
    await db_session.commit()


@pytest.mark.asyncio
async def test_update_moves_npm_out_of_network(client, db_session, test_settings):
    from nodvard_deck.models import DashboardLayout, Secret

    proposal_id = await _installation_before(db_session, test_settings)
    await _boot(db_session, test_settings)
    db_session.expire_all()

    npm = await db_session.get(ExtensionRecord, "npm")
    assert npm.state == "enabled", npm.last_error
    assert npm.settings == OLD_NETWORK["npm"]
    assert (await db_session.get(ExtensionRecord, "network")).settings == OLD_NETWORK, "die alte Zeile bleibt für den Rückweg"
    layout = (await db_session.execute(select(DashboardLayout))).scalar_one()
    assert [(i["ext_id"], i["widget_id"], i["x"]) for i in layout.items] == [("npm", "certificates", 2)]
    secret = (await db_session.execute(select(Secret).where(Secret.label == "network-npm-password"))).scalar_one()
    assert secret.owner_ext_id == "npm"
    adopted = (await db_session.execute(
        select(AuditEntry).where(AuditEntry.action == "extension.adopted", AuditEntry.target_id == "npm")
    )).scalar_one()
    assert adopted.detail == {
        "from": "network", "settings": True, "enabled": True, "widgets": 1, "secrets": 1, "extra_permissions": [],
    }
    runtime = get_extension_runtime()
    assert {"npm", "network"} <= set(runtime.loaded), "„Netzwerk“ bleibt als Wegweiser geladen"
    assert runtime.scheduler.get("npm", "cert-watch") is not None

    fake = FakeServers()
    http = runtime.loaded["npm"].ctx.http
    http._client = http._insecure_client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    headers = await _login(client)
    body = (await client.get("/api/v1/ext/npm/summary", headers=headers)).json()
    assert body["state"] == "ok", body
    assert (await client.get("/api/v1/ext/network/npm", headers=headers)).json() == body
    certs = (await client.get("/api/v1/ext/network/widgets/certificates", headers=headers)).json()
    assert certs == (await client.get("/api/v1/ext/npm/widgets/certificates", headers=headers)).json()
    assert (await client.get("/api/v1/ext/network/npm/address-check", headers=headers)).status_code == 200
    assert (await client.post("/api/v1/ext/network/npm/hosts/0/enable", headers=headers)).status_code == 422
    pages = {p["ext_id"]: p for p in (await client.get("/api/v1/pages", headers=headers)).json()}
    assert (pages["npm"]["component"], pages["network"]["component"]) == ("NpmPage", "NetworkPage")

    approved = await _approve(client, headers, proposal_id)
    assert (approved["status"], approved["action_type"]) == ("succeeded", "network.npm_host_disable")
    assert fake.npm_calls == ["disable 2"]
    shell = runtime.loaded["network"]
    assert (await shell.instance.health(shell.ctx)).healthy is True


@pytest.mark.asyncio
@pytest.mark.parametrize(("state", "settings"), [
    ("enabled", {"pihole": {"url": "http://pihole.test"}}),
    ("disabled", OLD_NETWORK),
    ("enabled", {"npm": {"url": "", "identity": "", "notify_expiring": True}}),
])
async def test_npm_only_starts_where_it_was_in_use(client, db_session, test_settings, state, settings):
    await _installation_before(db_session, test_settings, state=state, settings=settings)
    await _boot(db_session, test_settings)
    db_session.expire_all()
    assert (await db_session.get(ExtensionRecord, "npm")).state == "disabled"
    assert "npm" not in get_extension_runtime().loaded
