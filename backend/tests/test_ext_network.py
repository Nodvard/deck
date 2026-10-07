"""network-Extension ("Netzwerk") durch den echten Kern: Entdecken (startet
ausgeschaltet), Einschalten, Einstellungen + Geheimnisse ueber die Einstellungs-API,
Routen, Kacheln, Aktionen durchs Gate (vorschlagen -> freigeben -> Executor) und der
taegliche Zertifikats-Waechter.

Pi-hole und Nginx Proxy Manager sind Nachbauten hinter `httpx.MockTransport`, die in
`ctx.http` der geladenen Extension eingesetzt werden -- kein echter Server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import Action, AuditEntry, ExtensionRecord
from nodvard_deck.services import extensions as extensions_service
from sqlalchemy import select

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
PIHOLE_URL = "http://pihole.test"
NPM_URL = "http://npm.test:81"
PIHOLE_PASSWORD = "pi-geheim-123"
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
    """Pi-hole v6 + Nginx Proxy Manager, nach Host unterschieden."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.down: set[str] = set()
        self.pihole_sessions: set[str] = set()
        self.blocking = {"blocking": "enabled", "timer": None}
        self.blocking_posts: list[dict] = []
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
        if request.url.host == "pihole.test":
            return self._pihole(request)
        if request.url.host == "npm.test":
            return self._npm(request)
        return httpx.Response(404)

    def _pihole(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth" and request.method == "POST":
            if _body(request).get("password") != PIHOLE_PASSWORD:
                return httpx.Response(401, json={"error": {"key": "unauthorized", "message": "Unauthorized"}})
            sid = f"sid-{len(self.pihole_sessions) + 1}"
            self.pihole_sessions.add(sid)
            return httpx.Response(200, json={"session": {"valid": True, "totp": False, "sid": sid, "validity": 1800}})
        if request.headers.get("X-FTL-SID") not in self.pihole_sessions:
            return httpx.Response(401, json={"error": {"key": "unauthorized", "message": "Unauthorized"}})
        if path == "/api/auth" and request.method == "DELETE":
            self.pihole_sessions.discard(request.headers["X-FTL-SID"])
            return httpx.Response(204)
        if path == "/api/stats/summary":
            return httpx.Response(200, json={
                "queries": {"total": 20000, "blocked": 3000, "percent_blocked": 15.0},
                "clients": {"active": 9}, "gravity": {"domains_being_blocked": 250000, "last_update": 1790000000},
            })
        if path == "/api/dns/blocking":
            if request.method == "POST":
                body = _body(request)
                self.blocking_posts.append(body)
                self.blocking = {"blocking": "enabled" if body["blocking"] else "disabled", "timer": body.get("timer")}
            return httpx.Response(200, json=self.blocking)
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
    enabled = await client.post("/api/v1/extensions/network/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text

    fake = FakeServers()
    http = get_extension_runtime().loaded["network"].ctx.http
    mocked = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    http._client = mocked
    http._insecure_client = mocked
    return headers, fake


async def _configure(client, headers, *, pihole: bool = True, npm: bool = True) -> None:
    values: dict = {}
    if pihole:
        values["pihole"] = {"url": PIHOLE_URL + "/admin/"}
    if npm:
        values["npm"] = {"url": NPM_URL, "identity": "admin@home.example"}
    saved = await client.put("/api/v1/extensions/network/settings", json={"values": values}, headers=headers)
    assert saved.status_code == 200, saved.text
    secrets = []
    if pihole:
        secrets.append(("network-pihole-password", PIHOLE_PASSWORD))
    if npm:
        secrets.append(("network-npm-password", NPM_PASSWORD))
    for label, value in secrets:
        res = await client.put("/api/v1/extensions/network/secrets", json={"label": label, "value": value}, headers=headers)
        assert res.status_code == 204, res.text


async def _approve(client, headers, action_id: str) -> dict:
    res = await client.post(f"/api/v1/actions/{action_id}/approve", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------------
# Laden, Katalog, "nicht eingerichtet"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_network_is_discovered_disabled_and_loads_cleanly(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    record = await db_session.get(ExtensionRecord, "network")
    assert record is not None and record.state == "disabled"

    headers, _ = await _enable(client, db_session, test_settings)
    assert record.state == "enabled", record.last_error
    assert "network" in get_extension_runtime().loaded

    pages = (await client.get("/api/v1/pages", headers=headers)).json()
    page = next(p for p in pages if p["ext_id"] == "network")
    assert (page["path"], page["component"], page["title"]) == ("/network", "NetworkPage", "Netzwerk")
    widgets = {w["id"]: w for w in (await client.get("/api/v1/widgets", headers=headers)).json() if w["ext_id"] == "network"}
    assert set(widgets) == {"pihole", "certificates"}
    assert widgets["pihole"]["view"]["item"]["badge"] == {"text": "{{ label }}", "tone": "{{ tone }}"}

    bundle = await client.get("/api/v1/extensions/network/frontend/index.js")
    assert bundle.status_code == 200
    assert "NetworkPage" in bundle.text


@pytest.mark.asyncio
async def test_nothing_configured_is_reported_cleanly_everywhere(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)

    pihole = (await client.get("/api/v1/ext/network/pihole", headers=headers)).json()
    npm = (await client.get("/api/v1/ext/network/npm", headers=headers)).json()
    assert pihole["state"] == "not_configured" and "Einstellungen" in pihole["message"]
    assert npm["state"] == "not_configured" and npm["hosts"] == []

    widget = (await client.get("/api/v1/ext/network/widgets/pihole", headers=headers)).json()
    assert widget["data"] == [{"title": "Pi-hole nicht eingerichtet", "subtitle": pihole["message"], "label": "nicht eingerichtet", "tone": "neutral"}]
    certs = (await client.get("/api/v1/ext/network/widgets/certificates", headers=headers)).json()
    assert certs["data"][0]["title"] == "Nginx Proxy Manager nicht eingerichtet"

    pause = await client.post("/api/v1/ext/network/pihole/pause", json={"minutes": 5}, headers=headers)
    assert pause.status_code == 409
    assert pause.json()["detail"] == "Pi-hole ist nicht eingerichtet."
    enable = await client.post("/api/v1/ext/network/npm/hosts/1/enable", headers=headers)
    assert enable.status_code == 409

    health = await get_extension_runtime().loaded["network"].instance.health(get_extension_runtime().loaded["network"].ctx)
    assert health.healthy is False and "Noch nichts eingerichtet" in health.message
    assert fake.requests == [], "ohne Einstellungen wird nichts angefragt"
    assert (await db_session.execute(select(Action))).scalars().all() == []


@pytest.mark.asyncio
async def test_npm_with_address_but_without_password_says_what_is_missing(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    saved = await client.put(
        "/api/v1/extensions/network/settings",
        json={"values": {"npm": {"url": NPM_URL, "identity": "admin@home.example"}}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    npm = (await client.get("/api/v1/ext/network/npm", headers=headers)).json()
    assert npm["state"] == "not_configured"
    assert "fehlt noch das Passwort" in npm["message"]
    assert fake.requests == []


@pytest.mark.asyncio
async def test_routes_reject_anonymous_and_viewers_cannot_propose(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    routes = [
        ("GET", "/api/v1/ext/network/pihole"),
        ("GET", "/api/v1/ext/network/npm"),
        ("GET", "/api/v1/ext/network/widgets/pihole"),
        ("GET", "/api/v1/ext/network/widgets/certificates"),
        ("POST", "/api/v1/ext/network/pihole/pause"),
        ("POST", "/api/v1/ext/network/pihole/resume"),
        ("POST", "/api/v1/ext/network/npm/hosts/1/enable"),
        ("POST", "/api/v1/ext/network/npm/hosts/1/disable"),
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
    assert (await client.get("/api/v1/ext/network/pihole", headers=viewer)).status_code == 200
    assert (await client.post("/api/v1/ext/network/pihole/pause", json={"minutes": 5}, headers=viewer)).status_code == 403
    assert (await client.post("/api/v1/ext/network/npm/hosts/1/disable", headers=viewer)).status_code == 403
    assert (await db_session.execute(select(Action))).scalars().all() == []
    assert fake.blocking_posts == [] and fake.npm_calls == []


# ---------------------------------------------------------------------------
# Pi-hole
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pihole_status_and_widget_after_setup_without_leaking_the_password(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)

    settings = (await client.get("/api/v1/extensions/network/settings", headers=headers)).json()
    assert {s["label"]: s["is_set"] for s in settings["secrets"]} == {"network-pihole-password": True, "network-npm-password": True}

    res = await client.get("/api/v1/ext/network/pihole", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "ok", body
    assert body["url"] == PIHOLE_URL, "…/admin/ wird abgeschnitten"
    assert body["summary"]["queries_total"] == 20000
    assert body["summary"]["percent_blocked"] == 15.0
    assert body["summary"]["domains_blocked"] == 250000
    assert body["blocking"] == {"status": "enabled", "enabled": True, "timer_s": None}

    widget = (await client.get("/api/v1/ext/network/widgets/pihole", headers=headers)).json()["data"]
    assert widget[0] == {"title": "Blockierung", "subtitle": "Werbung und Tracker werden blockiert.", "label": "aktiv", "tone": "good"}
    assert widget[1]["subtitle"] == "20.000 Anfragen, davon 3.000 blockiert"
    assert widget[1]["label"] == "15,0 % blockiert"
    assert widget[2]["label"] == "250.000"

    # Sitzung wird wiederverwendet: EINE Anmeldung fuer alle Aufrufe.
    assert sum(1 for r in fake.requests if r.url.path == "/api/auth") == 1
    everything = res.text + json.dumps(settings) + json.dumps(widget) + caplog.text
    assert PIHOLE_PASSWORD not in everything and NPM_PASSWORD not in everything
    audit = (await db_session.execute(select(AuditEntry))).scalars().all()
    assert all(PIHOLE_PASSWORD not in json.dumps(a.detail or {}) for a in audit)


@pytest.mark.asyncio
async def test_pihole_pause_goes_through_the_gate_and_reaches_the_pihole(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, npm=False)

    proposed = await client.post("/api/v1/ext/network/pihole/pause", json={"minutes": 15}, headers=headers)
    assert proposed.status_code == 200, proposed.text
    body = proposed.json()
    assert (body["status"], body["risk"]) == ("proposed", "low")
    assert fake.blocking_posts == [], "vor der Freigabe passiert nichts"

    approved = await _approve(client, headers, body["action_id"])
    assert approved["status"] == "succeeded", approved
    assert approved["result"]["output"] == "Pi-hole-Blockierung für 15 Minuten pausiert."
    assert fake.blocking_posts == [{"blocking": False, "timer": 900}]
    assert approved["payload"] == {"minutes": 15}
    assert "15 Minuten" in approved["reason"]

    status = (await client.get("/api/v1/ext/network/pihole", headers=headers)).json()
    assert status["blocking"] == {"status": "disabled", "enabled": False, "timer_s": 900}
    widget = (await client.get("/api/v1/ext/network/widgets/pihole", headers=headers)).json()["data"]
    assert widget[0] == {"title": "Blockierung", "subtitle": "Pausiert – noch 15 Min.", "label": "pausiert", "tone": "warn"}

    resumed = await client.post("/api/v1/ext/network/pihole/resume", headers=headers)
    assert resumed.json()["risk"] == "low"
    approved = await _approve(client, headers, resumed.json()["action_id"])
    assert approved["status"] == "succeeded"
    assert approved["result"]["output"] == "Pi-hole blockiert wieder."
    assert fake.blocking_posts[-1] == {"blocking": True, "timer": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("minutes", [0, 7, 1440, -5])
async def test_pause_route_only_accepts_the_fixed_lengths(client, db_session, test_settings, minutes):
    headers, _fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, npm=False)
    res = await client.post("/api/v1/ext/network/pihole/pause", json={"minutes": minutes}, headers=headers)
    assert res.status_code == 422
    assert (await db_session.execute(select(Action))).scalars().all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_type", "payload", "error"),
    [
        ("network.pihole_pause", {"minutes": 7}, "5, 15 oder 60"),
        ("network.pihole_pause", {"minutes": "15"}, "ganze Zahl"),
        ("network.pihole_pause", {"minutes": True}, "ganze Zahl"),
        ("network.pihole_pause", {"minutes": 15, "timer": 99999}, "unerwartete Angaben (timer)"),
        ("network.pihole_resume", {"blocking": False}, "unerwartete Angaben"),
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

    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    ctx = get_extension_runtime().loaded["network"].ctx
    decision = await ctx.actions.propose(ActionRequest(
        action_type=action_type, payload=payload, risk=Risk.LOW, proposed_by=Actor.user("x", "x"), reason="Test",
    ))
    approved = await _approve(client, headers, decision.action_id)
    assert approved["status"] == "failed", approved
    assert error in approved["result"]["error"]
    assert fake.blocking_posts == [] and fake.npm_calls == []


@pytest.mark.asyncio
async def test_pihole_unreachable_and_wrong_password_are_clean_states(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, npm=False)
    fake.down.add("pihole.test")

    body = (await client.get("/api/v1/ext/network/pihole", headers=headers)).json()
    assert body["state"] == "unreachable"
    assert body["message"].startswith("Pi-hole ist nicht erreichbar")
    widget = (await client.get("/api/v1/ext/network/widgets/pihole", headers=headers)).json()["data"]
    assert widget == [{"title": "Pi-hole nicht erreichbar", "subtitle": body["message"], "label": "nicht erreichbar", "tone": "danger"}]

    # Pause trotz Ausfall: Vorschlag geht, die Ausfuehrung scheitert sauber.
    proposed = (await client.post("/api/v1/ext/network/pihole/pause", json={"minutes": 5}, headers=headers)).json()
    approved = await _approve(client, headers, proposed["action_id"])
    assert approved["status"] == "failed"
    assert "nicht erreichbar" in approved["result"]["error"]

    fake.down.clear()
    res = await client.put(
        "/api/v1/extensions/network/secrets", json={"label": "network-pihole-password", "value": "falsch"}, headers=headers,
    )
    assert res.status_code == 204
    fake.pihole_sessions.clear()  # alte Sitzung abgelaufen -> neue Anmeldung mit dem falschen Passwort
    body = (await client.get("/api/v1/ext/network/pihole", headers=headers)).json()
    assert body["state"] == "auth_failed"
    assert "Passwort" in body["message"]
    widget = (await client.get("/api/v1/ext/network/widgets/pihole", headers=headers)).json()["data"]
    assert widget[0]["label"] == "Anmeldung fehlgeschlagen" and widget[0]["tone"] == "danger"


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

    loaded = get_extension_runtime().loaded["network"]
    http = loaded.ctx.http
    http._client = http._insecure_client = httpx.AsyncClient(transport=httpx.MockTransport(redirect))

    pihole_res = await client.get("/api/v1/ext/network/pihole", headers=headers)
    npm_res = await client.get("/api/v1/ext/network/npm", headers=headers)
    assert pihole_res.status_code == 200 and npm_res.status_code == 200, (pihole_res.text, npm_res.text)
    pihole, npm = pihole_res.json(), npm_res.json()
    for body, service in ((pihole, "Pi-hole"), (npm, "Nginx Proxy Manager")):
        assert body["state"] == "unreachable"
        assert body["message"].startswith(f"{service} {sentence}")
    widgets = []
    for name in ("pihole", "certificates"):
        res = await client.get(f"/api/v1/ext/network/widgets/{name}", headers=headers)
        assert res.status_code == 200, (name, res.text)
        assert "leitet auf eine andere Adresse um" in res.text, name
        widgets.append(res.text)
    proposed = (await client.post("/api/v1/ext/network/pihole/pause", json={"minutes": 5}, headers=headers)).json()
    approved = await _approve(client, headers, proposed["action_id"])
    assert approved["status"] == "failed"
    assert "leitet auf eine andere Adresse um" in approved["result"]["error"]
    health = await loaded.instance.health(loaded.ctx)
    assert health.healthy is False
    watch = await get_extension_runtime().scheduler.get("network", "cert-watch")()
    assert "leitet auf eine andere Adresse um" in watch["skipped"]
    everything = json.dumps([pihole, npm, widgets, approved["result"], health.message, watch]) + caplog.text
    assert "angreifer" not in everything.lower()
    assert "Traceback" not in caplog.text
    assert all(r.url.host != "angreifer.example" for r in seen)


@pytest.mark.asyncio
async def test_changing_the_address_logs_the_old_session_out(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, npm=False)
    assert (await client.get("/api/v1/ext/network/pihole", headers=headers)).json()["state"] == "ok"
    assert fake.pihole_sessions == {"sid-1"}

    saved = await client.put("/api/v1/extensions/network/settings", json={"values": {"pihole": {}}}, headers=headers)
    assert saved.status_code == 200
    assert fake.pihole_sessions == set(), "Pi-hole hat nur wenige API-Plaetze -- alte Sitzung freigeben"
    assert [f"{r.method} {r.url.path}" for r in fake.requests][-1] == "DELETE /api/auth"
    assert (await client.get("/api/v1/ext/network/pihole", headers=headers)).json()["state"] == "not_configured"


@pytest.mark.asyncio
async def test_simultaneous_requests_share_one_pihole_session(client, db_session, test_settings):
    """Seite und Kacheln fragen gleichzeitig -- es darf nur EINE Sitzung entstehen,
    auch direkt nach dem Start (Pi-hole hat nur wenige API-Plaetze)."""
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, npm=False)
    service = get_extension_runtime().loaded["network"].instance._service
    await service.close()
    fake.pihole_sessions.clear()

    results = await asyncio.gather(*(service.pihole_status() for _ in range(4)))
    assert [r["state"] for r in results] == ["ok"] * 4
    assert fake.pihole_sessions == {"sid-1"}
    assert sum(1 for r in fake.requests if r.method == "POST" and r.url.path == "/api/auth") == 1


# ---------------------------------------------------------------------------
# Nginx Proxy Manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_npm_hosts_certificates_and_widget(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, pihole=False)

    body = (await client.get("/api/v1/ext/network/npm", headers=headers)).json()
    assert body["state"] == "ok", body
    assert body["summary"] == {"hosts": 3, "hosts_enabled": 2, "certificates": 3, "certificates_warn": 1, "certificates_expired": 1}
    assert [h["domains"][0] for h in body["hosts"]] == ["alt.home.example", "cloud.home.example", "nas.home.example"]
    nas = next(h for h in body["hosts"] if h["id"] == 2)
    assert nas["target"] == "https://192.168.1.10:5001"
    assert nas["certificate"]["status"] == "warn"
    assert nas["certificate"]["days_text"] == "noch 5 Tage"
    assert [c["status"] for c in body["certificates"]] == ["expired", "warn", "ok"], "dringendste zuerst"

    rows = (await client.get("/api/v1/ext/network/widgets/certificates", headers=headers)).json()["data"]
    assert [(r["title"], r["label"], r["tone"]) for r in rows] == [
        ("Altes Zertifikat", "abgelaufen", "danger"),
        ("nas.home.example", "läuft bald ab", "warn"),
    ]
    assert "abgelaufen (seit 3 Tagen abgelaufen)" in rows[0]["subtitle"]

    # Alles erneuert -> eine gruene Zeile statt einer leeren Kachel.
    for cert in fake.certs:
        cert["expires_on"] = _expiry(80)
    rows = (await client.get("/api/v1/ext/network/widgets/certificates", headers=headers)).json()["data"]
    assert len(rows) == 1 and rows[0]["tone"] == "good" and rows[0]["title"].startswith("Alle Zertifikate gültig")
    assert sum(1 for r in fake.requests if r.url.path == "/api/tokens") == 1


@pytest.mark.asyncio
async def test_npm_disable_host_through_the_gate(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, pihole=False)

    proposed = await client.post("/api/v1/ext/network/npm/hosts/2/disable", headers=headers)
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
    again = await client.post("/api/v1/ext/network/npm/hosts/2/disable", headers=headers)
    assert again.status_code == 409 and "schon ausgeschaltet" in again.json()["detail"]
    assert (await client.post("/api/v1/ext/network/npm/hosts/99/enable", headers=headers)).status_code == 404
    assert (await client.post("/api/v1/ext/network/npm/hosts/0/enable", headers=headers)).status_code == 422

    enable = (await client.post("/api/v1/ext/network/npm/hosts/2/enable", headers=headers)).json()
    approved = await _approve(client, headers, enable["action_id"])
    assert approved["status"] == "succeeded"
    assert fake.npm_calls == ["disable 2", "enable 2"]


@pytest.mark.asyncio
async def test_npm_unreachable_is_a_clean_state_and_blocks_proposals(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, pihole=False)
    fake.down.add("npm.test")

    body = (await client.get("/api/v1/ext/network/npm", headers=headers)).json()
    assert body["state"] == "unreachable" and body["hosts"] == []
    rows = (await client.get("/api/v1/ext/network/widgets/certificates", headers=headers)).json()["data"]
    assert rows[0]["title"] == "Nginx Proxy Manager nicht erreichbar" and rows[0]["tone"] == "danger"
    res = await client.post("/api/v1/ext/network/npm/hosts/1/disable", headers=headers)
    assert res.status_code == 502
    assert "nicht erreichbar" in res.json()["detail"]

    health = await get_extension_runtime().loaded["network"].instance.health(get_extension_runtime().loaded["network"].ctx)
    assert health.healthy is False and "Nginx Proxy Manager" in health.message


@pytest.mark.asyncio
async def test_npm_fehler_ohne_text_liefert_trotzdem_einen_grund(client, db_session, test_settings, monkeypatch):
    """Ein 502 mit leerem `detail` sieht fuer die Seite wie eine Proxy-Fehlerseite aus
    ("Server gerade nicht erreichbar"). Deshalb bekommt es mindestens den Namen des Fehlers."""
    headers, _fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, pihole=False)
    npm = sys.modules["nodvard_deck_ext_network.npm"]

    async def kaputt(self, certificates=None):
        raise npm.NpmError("")

    monkeypatch.setattr(npm.NpmClient, "proxy_hosts", kaputt)
    res = await client.post("/api/v1/ext/network/npm/hosts/1/disable", headers=headers)
    assert res.status_code == 502
    assert res.json()["detail"] == "NpmError"


# ---------------------------------------------------------------------------
# Zertifikats-Waechter
# ---------------------------------------------------------------------------


async def _cert_notifications(db_session) -> list[tuple[str, str]]:
    from nodvard_deck.models import Notification as NotificationRow

    rows = (await db_session.execute(
        select(NotificationRow).where(NotificationRow.source_ext_id == "network").order_by(NotificationRow.ts)
    )).scalars().all()
    return [(r.severity, r.title) for r in rows]


@pytest.mark.asyncio
async def test_cert_watch_notifies_once_per_certificate_and_day(client, db_session, test_settings):
    headers, _fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, pihole=False)
    loaded = get_extension_runtime().loaded["network"]
    handler = get_extension_runtime().scheduler.get("network", "cert-watch")
    assert handler is not None

    assert (await handler())["notified"] == 2
    assert (await handler())["notified"] == 0, "am selben Tag keine zweite Meldung"
    notes = await _cert_notifications(db_session)
    assert sorted(notes) == [("critical", "Zertifikat abgelaufen: Altes Zertifikat"), ("warning", "Zertifikat läuft bald ab: nas.home.example")]

    certwatch = sys.modules["nodvard_deck_ext_network.certwatch"]
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    result = await certwatch.run_cert_watch(loaded.ctx, loaded.instance._service, now=tomorrow)
    assert result["notified"] == 2, "am naechsten Tag wieder, solange nicht erneuert"

    # Abschaltbar in den Einstellungen.
    saved = await client.put(
        "/api/v1/extensions/network/settings",
        json={"values": {"npm": {"url": NPM_URL, "identity": "admin@home.example", "notify_expiring": False}}}, headers=headers,
    )
    assert saved.status_code == 200
    assert (await certwatch.run_cert_watch(loaded.ctx, loaded.instance._service, now=tomorrow + timedelta(days=1)))["notified"] == 0


@pytest.mark.asyncio
async def test_cert_watch_is_quiet_when_not_configured(client, db_session, test_settings):
    await _enable(client, db_session, test_settings)
    handler = get_extension_runtime().scheduler.get("network", "cert-watch")
    assert (await handler())["notified"] == 0
    assert await _cert_notifications(db_session) == []
