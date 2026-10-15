"""Pi-hole durch den echten Kern: Laden, Einstellungen, Routen, Kachel, Aktionen über das Gate und der Umzug aus
„Netzwerk“ mit dem Stand einer bestehenden Installation. Pi-hole selbst ist ein Nachbau hinter `httpx.MockTransport`."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx
import pytest
from nodvard_deck.core import vault
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import (
    Action,
    AuditEntry,
    DashboardLayout,
    ExtensionRecord,
    Secret,
)
from nodvard_deck.services import extensions as extensions_service
from sqlalchemy import select

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pihole"
PIHOLE_URL = "http://pihole.test"
PASSWORD = "pi-geheim-123"
SECRET = "network-pihole-password"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content) if request.content else {}


class FakePihole:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.down = False
        self.sessions: set[str] = set()
        self.blocking = {"blocking": "enabled", "timer": None}
        self.changes: list[str] = []
        self.domains: dict[str, set[str]] = {"allow": set(), "deny": set()}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("[Errno 111] Connection refused")
        path = request.url.path
        if path == "/api/auth" and request.method == "POST":
            if _body(request).get("password") != PASSWORD:
                return httpx.Response(401, json={"error": {"key": "unauthorized", "message": "Unauthorized"}})
            sid = f"sid-{len(self.sessions) + 1}"
            self.sessions.add(sid)
            answer = _fixture("auth.json")
            answer["session"]["sid"] = sid
            return httpx.Response(200, json=answer)
        if request.headers.get("X-FTL-SID") not in self.sessions:
            return httpx.Response(401, json={"error": {"key": "unauthorized", "message": "Unauthorized"}})
        if path == "/api/auth" and request.method == "DELETE":
            self.sessions.discard(request.headers["X-FTL-SID"])
            return httpx.Response(204)
        if path == "/api/stats/summary":
            return httpx.Response(200, json=_fixture("stats_summary.json"))
        if path == "/api/stats/top_clients":
            return httpx.Response(200, json=_fixture("top_clients.json"))
        if path == "/api/dns/blocking":
            if request.method == "POST":
                body = _body(request)
                self.changes.append(f"blocking {body}")
                self.blocking = {"blocking": "enabled" if body["blocking"] else "disabled", "timer": body.get("timer")}
            return httpx.Response(200, json=self.blocking)
        if path == "/api/action/gravity" and request.method == "POST":
            self.changes.append("gravity")
            return httpx.Response(200, content=(FIXTURES / "gravity.txt").read_bytes(), headers={"Content-Type": "text/plain"})
        for kind in ("allow", "deny"):
            if path == f"/api/domains/{kind}/exact" and request.method == "POST":
                domain = _body(request)["domain"]
                self.changes.append(f"{kind} {domain}")
                if domain in self.domains[kind]:
                    return httpx.Response(201, json=_fixture("domains_duplicate.json"))
                self.domains[kind].add(domain)
                return httpx.Response(201, json=_fixture("domains_added.json"))
        return httpx.Response(404)


async def _login(client, username="owner1", password="correct-horse-battery") -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _mock(fake: FakePihole) -> None:
    http = get_extension_runtime().loaded["pihole"].ctx.http
    http._client = http._insecure_client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))


async def _enable(client, db_session, test_settings) -> tuple[dict, FakePihole]:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    headers = await _login(client)
    enabled = await client.post("/api/v1/extensions/pihole/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text
    fake = FakePihole()
    _mock(fake)
    return headers, fake


async def _configure(client, headers, url: str = PIHOLE_URL + "/admin/") -> None:
    saved = await client.put("/api/v1/extensions/pihole/settings", json={"current_password": "correct-horse-battery", "values": {"url": url}}, headers=headers)
    assert saved.status_code == 200, saved.text
    res = await client.put("/api/v1/extensions/pihole/secrets", json={"current_password": "correct-horse-battery", "label": SECRET, "value": PASSWORD}, headers=headers)
    assert res.status_code == 204, res.text


async def _approve(client, headers, action_id: str) -> dict:
    res = await client.post(f"/api/v1/actions/{action_id}/approve", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.asyncio
async def test_pihole_is_discovered_disabled_and_loads_cleanly(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    record = await db_session.get(ExtensionRecord, "pihole")
    assert record is not None and record.state == "disabled"
    assert record.manifest["stage"] == "beta"

    headers, _ = await _enable(client, db_session, test_settings)
    assert record.state == "enabled", record.last_error
    pages = (await client.get("/api/v1/pages", headers=headers)).json()
    page = next(p for p in pages if p["ext_id"] == "pihole")
    assert (page["path"], page["component"], page["title"]) == ("/pihole", "PiholePage", "Pi-hole")
    widgets = {w["id"]: w for w in (await client.get("/api/v1/widgets", headers=headers)).json() if w["ext_id"] == "pihole"}
    assert set(widgets) == {"status"}
    bundle = await client.get("/api/v1/extensions/pihole/frontend/index.js")
    assert bundle.status_code == 200 and "PiholePage" in bundle.text


@pytest.mark.asyncio
async def test_nothing_configured_is_reported_cleanly_everywhere(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    summary = (await client.get("/api/v1/ext/pihole/summary", headers=headers)).json()
    assert summary["state"] == "not_configured" and "Erweiterungen → Pi-hole" in summary["message"]
    assert summary["top_clients"] is None
    widget = (await client.get("/api/v1/ext/pihole/widgets/status", headers=headers)).json()
    assert widget["data"] == [{"title": "Pi-hole nicht eingerichtet", "subtitle": summary["message"], "label": "nicht eingerichtet", "tone": "neutral"}]
    for path, body in (("pause", {"minutes": 5}), ("resume", None), ("gravity", None), ("domains", {"list": "deny", "domain": "a.example.com"})):
        res = await client.post(f"/api/v1/ext/pihole/{path}", json=body, headers=headers)
        assert (res.status_code, res.json()["detail"]) == (409, "Pi-hole ist nicht eingerichtet."), path
    loaded = get_extension_runtime().loaded["pihole"]
    health = await loaded.instance.health(loaded.ctx)
    assert health.healthy is False and "noch nicht eingerichtet" in health.message
    suggestions = (await client.get("/api/v1/apps/suggestions", headers=headers)).json()
    assert all(s["source"] != "Pi-hole" for s in suggestions["suggestions"]), suggestions
    assert fake.requests == []
    assert (await db_session.execute(select(Action))).scalars().all() == []


@pytest.mark.asyncio
async def test_routes_reject_anonymous_and_viewers_cannot_propose(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    routes = [("GET", "summary"), ("GET", "widgets/status"), ("POST", "pause"), ("POST", "resume"), ("POST", "gravity"), ("POST", "domains")]
    results = {f"{m} {p}": (await client.request(m, f"/api/v1/ext/pihole/{p}", json={"minutes": 5})).status_code for m, p in routes}
    assert results == {key: 401 for key in results}

    roles = {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers=headers)).json()}
    created = await client.post(
        "/api/v1/users", json={"username": "gast", "password": "gast-passwort-1", "role_ids": [roles["viewer"]]}, headers=headers,
    )
    assert created.status_code == 201, created.text
    login = await client.post("/api/v1/auth/login", json={"username": "gast", "password": "gast-passwort-1"})
    viewer = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get("/api/v1/ext/pihole/summary", headers=viewer)).status_code == 200
    for path, body in (("pause", {"minutes": 5}), ("gravity", None), ("domains", {"list": "allow", "domain": "a.example.com"})):
        assert (await client.post(f"/api/v1/ext/pihole/{path}", json=body, headers=viewer)).status_code == 403, path
    assert (await db_session.execute(select(Action))).scalars().all() == []
    assert fake.changes == []


@pytest.mark.asyncio
async def test_status_widget_and_suggestion_without_leaking_the_password(client, db_session, test_settings, caplog):
    caplog.set_level(logging.DEBUG)
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)

    res = await client.get("/api/v1/ext/pihole/summary", headers=headers)
    body = res.json()
    assert body["state"] == "ok", body
    assert body["url"] == PIHOLE_URL
    assert body["summary"]["queries_total"] == 29160 and body["summary"]["percent_blocked"] == 21.9
    assert body["blocking"] == {"status": "enabled", "enabled": True, "timer_s": None}
    assert [c["name"] for c in body["top_clients"]] == ["fernseher.lan", "192.0.2.12", "laptop.lan", "localhost", "handy.lan"]

    widget = (await client.get("/api/v1/ext/pihole/widgets/status", headers=headers)).json()["data"]
    assert widget[0] == {"title": "Blockierung", "subtitle": "Werbung und Tracker werden blockiert.", "label": "aktiv", "tone": "good"}
    assert widget[1]["subtitle"] == "29.160 Anfragen, davon 6.379 blockiert"
    assert widget[1]["label"] == "21,9 % blockiert"
    assert widget[2]["label"] == "104.642"
    assert widget[3] == {
        "title": "Aktivste Geräte", "subtitle": "fernseher.lan (5.896), 192.0.2.12 (4.120), laptop.lan (3.311)",
        "label": "5 Geräte", "tone": "neutral",
    }

    suggestions = (await client.get("/api/v1/apps/suggestions", headers=headers)).json()
    assert [s for s in suggestions["suggestions"] if s["source"] == "Pi-hole"] == [
        {"name": "Pi-hole", "url": "http://pihole.test/admin/", "source": "Pi-hole", "host": None},
    ]

    assert sum(1 for r in fake.requests if r.url.path == "/api/auth") == 1, "eine Sitzung für alle Aufrufe"
    loaded = get_extension_runtime().loaded["pihole"]
    assert (await loaded.instance.health(loaded.ctx)).healthy is True
    everything = res.text + json.dumps(widget) + json.dumps(suggestions) + caplog.text
    assert PASSWORD not in everything
    audit = (await db_session.execute(select(AuditEntry))).scalars().all()
    assert all(PASSWORD not in json.dumps(a.detail or {}) for a in audit)


@pytest.mark.asyncio
async def test_pause_and_resume_keep_their_action_types(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)

    proposed = (await client.post("/api/v1/ext/pihole/pause", json={"minutes": 15}, headers=headers)).json()
    assert (proposed["status"], proposed["risk"]) == ("proposed", "low")
    assert fake.changes == [], "vor der Freigabe passiert nichts"
    approved = await _approve(client, headers, proposed["action_id"])
    assert approved["status"] == "succeeded", approved
    assert approved["action_type"] == "network.pihole_pause"
    assert approved["result"]["output"] == "Pi-hole-Blockierung für 15 Minuten pausiert."
    assert fake.changes == ["blocking {'blocking': False, 'timer': 900}"]

    widget = (await client.get("/api/v1/ext/pihole/widgets/status", headers=headers)).json()["data"]
    assert widget[0] == {"title": "Blockierung", "subtitle": "Pausiert – noch 15 Min.", "label": "pausiert", "tone": "warn"}

    resumed = (await client.post("/api/v1/ext/pihole/resume", headers=headers)).json()
    approved = await _approve(client, headers, resumed["action_id"])
    assert (approved["action_type"], approved["result"]["output"]) == ("network.pihole_resume", "Pi-hole blockiert wieder.")


@pytest.mark.asyncio
@pytest.mark.parametrize("minutes", [0, 7, 1440, -5])
async def test_pause_route_only_accepts_the_fixed_lengths(client, db_session, test_settings, minutes):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    res = await client.post("/api/v1/ext/pihole/pause", json={"minutes": minutes}, headers=headers)
    assert res.status_code == 422
    assert (await db_session.execute(select(Action))).scalars().all() == []


@pytest.mark.asyncio
async def test_gravity_update_goes_through_the_gate(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    proposed = (await client.post("/api/v1/ext/pihole/gravity", headers=headers)).json()
    assert (proposed["status"], proposed["risk"]) == ("proposed", "low")
    approved = await _approve(client, headers, proposed["action_id"])
    assert approved["status"] == "succeeded", approved
    assert approved["action_type"] == "pihole.gravity_update"
    output = approved["result"]["output"]
    assert output.startswith("Pi-hole hat die Sperrlisten neu geladen.\n\n") and output.endswith("[✓] Done.")
    assert "\x1b" not in output
    assert fake.changes == ["gravity"]


@pytest.mark.asyncio
async def test_domains_are_allowed_or_denied_through_the_gate(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)

    res = await client.post("/api/v1/ext/pihole/domains", json={"list": "deny", "domain": " Werbung.Example.com. "}, headers=headers)
    proposed = res.json()
    assert (proposed["status"], proposed["risk"]) == ("proposed", "medium")
    approved = await _approve(client, headers, proposed["action_id"])
    assert (approved["action_type"], approved["payload"]) == ("pihole.domain_deny", {"domain": "werbung.example.com"})
    assert approved["result"]["output"] == "„werbung.example.com“ ist jetzt in Pi-hole gesperrt."
    assert "werbung.example.com" in approved["reason"]

    again = (await client.post("/api/v1/ext/pihole/domains", json={"list": "deny", "domain": "werbung.example.com"}, headers=headers)).json()
    approved = await _approve(client, headers, again["action_id"])
    assert (approved["status"], approved["result"]["output"]) == ("succeeded", "„werbung.example.com“ stand schon auf der Liste (gesperrt).")

    allow = (await client.post("/api/v1/ext/pihole/domains", json={"list": "allow", "domain": "cdn.example.org"}, headers=headers)).json()
    approved = await _approve(client, headers, allow["action_id"])
    assert approved["action_type"] == "pihole.domain_allow"
    assert approved["result"]["output"] == "„cdn.example.org“ ist jetzt in Pi-hole erlaubt."
    assert fake.changes == ["deny werbung.example.com", "deny werbung.example.com", "allow cdn.example.org"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"list": "deny", "domain": "http://werbung.example.com/"},
    {"list": "deny", "domain": "*.example.com"},
    {"list": "deny", "domain": "localhost"},
    {"list": "regex", "domain": "werbung.example.com"},
    {"list": "deny"},
])
async def test_domain_route_refuses_odd_input(client, db_session, test_settings, body):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    res = await client.post("/api/v1/ext/pihole/domains", json=body, headers=headers)
    assert res.status_code == 422, res.text
    assert (await db_session.execute(select(Action))).scalars().all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("action_type", "payload", "error"), [
    ("network.pihole_pause", {"minutes": 7}, "5, 15 oder 60"),
    ("network.pihole_pause", {"minutes": "15"}, "ganze Zahl"),
    ("network.pihole_pause", {"minutes": True}, "ganze Zahl"),
    ("network.pihole_pause", {"minutes": 15, "timer": 99999}, "unerwartete Angaben (timer)"),
    ("network.pihole_resume", {"blocking": False}, "unerwartete Angaben"),
    ("pihole.gravity_update", {"force": True}, "unerwartete Angaben"),
    ("pihole.domain_deny", {"domain": "a.example.com; rm -rf /"}, "Bitte eine Domain"),
    ("pihole.domain_deny", {"domain": "A.example.com"}, "nicht in der gespeicherten Form"),
    ("pihole.domain_allow", {"domain": "a.example.com", "kind": "regex"}, "unerwartete Angaben (kind)"),
    ("pihole.domain_allow", {}, "Bitte eine Domain"),
])
async def test_executor_rejects_forged_payloads(client, db_session, test_settings, action_type, payload, error):
    from nodvard_sdk import Actor, Risk
    from nodvard_sdk.actions import ActionRequest

    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    ctx = get_extension_runtime().loaded["pihole"].ctx
    decision = await ctx.actions.propose(ActionRequest(
        action_type=action_type, payload=payload, risk=Risk.LOW, proposed_by=Actor.user("x", "x"), reason="Test",
    ))
    approved = await _approve(client, headers, decision.action_id)
    assert approved["status"] == "failed", approved
    assert error in approved["result"]["error"]
    assert fake.changes == []


@pytest.mark.asyncio
async def test_unreachable_and_wrong_password_are_clean_states(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.down = True

    body = (await client.get("/api/v1/ext/pihole/summary", headers=headers)).json()
    assert body["state"] == "unreachable" and body["message"].startswith("Pi-hole ist nicht erreichbar")
    widget = (await client.get("/api/v1/ext/pihole/widgets/status", headers=headers)).json()["data"]
    assert widget == [{"title": "Pi-hole nicht erreichbar", "subtitle": body["message"], "label": "nicht erreichbar", "tone": "danger"}]
    proposed = (await client.post("/api/v1/ext/pihole/gravity", headers=headers)).json()
    approved = await _approve(client, headers, proposed["action_id"])
    assert approved["status"] == "failed" and "nicht erreichbar" in approved["result"]["error"]

    fake.down = False
    res = await client.put("/api/v1/extensions/pihole/secrets", json={"current_password": "correct-horse-battery", "label": SECRET, "value": "falsch"}, headers=headers)
    assert res.status_code == 204
    fake.sessions.clear()
    body = (await client.get("/api/v1/ext/pihole/summary", headers=headers)).json()
    assert body["state"] == "auth_failed" and "Passwort" in body["message"]


@pytest.mark.asyncio
async def test_redirects_never_show_the_foreign_address(client, db_session, test_settings, caplog):
    caplog.set_level(logging.INFO)
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    seen: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://angreifer.example/login"})

    http = get_extension_runtime().loaded["pihole"].ctx.http
    http._client = http._insecure_client = httpx.AsyncClient(transport=httpx.MockTransport(redirect))
    body = (await client.get("/api/v1/ext/pihole/summary", headers=headers)).json()
    assert body["state"] == "unreachable" and body["message"].startswith("Pi-hole leitet auf eine andere Adresse um (HTTP 302).")
    widget = (await client.get("/api/v1/ext/pihole/widgets/status", headers=headers)).text
    proposed = (await client.post("/api/v1/ext/pihole/pause", json={"minutes": 5}, headers=headers)).json()
    approved = await _approve(client, headers, proposed["action_id"])
    assert approved["status"] == "failed"
    everything = json.dumps([body, widget, approved["result"]]) + caplog.text
    assert "angreifer" not in everything.lower() and "Traceback" not in caplog.text
    assert all(r.url.host != "angreifer.example" for r in seen)


@pytest.mark.asyncio
async def test_changing_the_address_logs_the_old_session_out(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    assert (await client.get("/api/v1/ext/pihole/summary", headers=headers)).json()["state"] == "ok"
    assert fake.sessions == {"sid-1"}

    saved = await client.put("/api/v1/extensions/pihole/settings", json={"current_password": "correct-horse-battery", "values": {"url": None}}, headers=headers)
    assert saved.status_code == 200
    assert fake.sessions == set(), "Pi-hole hat nur wenige API-Plätze"
    assert (await client.get("/api/v1/ext/pihole/summary", headers=headers)).json()["state"] == "not_configured"


@pytest.mark.asyncio
async def test_simultaneous_requests_share_one_session(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    service = get_extension_runtime().loaded["pihole"].instance._service
    await service.close()
    fake.sessions.clear()

    results = await asyncio.gather(*(service.status() for _ in range(4)))
    assert [r["state"] for r in results] == ["ok"] * 4
    assert sum(1 for r in fake.requests if r.method == "POST" and r.url.path == "/api/auth") == 1


# Stand einer Installation mit 0.8: Pi-hole steckt im Modul „Netzwerk“, das beim Einschalten diese Rechte bekam.
OLD_NETWORK_GRANTED = [
    "net.outbound", "net.outbound.insecure_tls", "hosts.execute", "secrets.read:network-*", "schedule.register",
    "notify.send",
]
OLD_NETWORK = {
    "pihole": {"url": PIHOLE_URL + "/admin/", "tls_insecure_skip_verify": False},
    "npm": {"url": "http://npm.test:81", "identity": "admin@home.example", "notify_expiring": True},
}


async def _installation_with_0_8(db_session, test_settings, *, state: str = "enabled", settings: dict | None = None) -> str:
    db_session.add(ExtensionRecord(
        id="network", version="0.1.0", api_version="0.1.0", state=state, source="bundled",
        settings=OLD_NETWORK if settings is None else settings, manifest={"id": "network", "name": "Netzwerk"},
        granted_permissions=OLD_NETWORK_GRANTED,
    ))
    db_session.add(DashboardLayout(user_id="u1", name="Standard", is_default=True, items=[
        {"ext_id": "network", "widget_id": "pihole", "x": 0, "y": 0, "w": 2, "h": 2, "config": {}},
        {"ext_id": "network", "widget_id": "certificates", "x": 2, "y": 0, "w": 2, "h": 2, "config": {}},
    ]))
    secret = await vault.create_secret(
        db_session, vault.load_keyring(test_settings), label=SECRET, kind="generic", plaintext=PASSWORD, owner_ext_id="network",
    )
    proposal = Action(
        ext_id="network", action_type="network.pihole_pause", payload={"minutes": 5}, risk="low", status="proposed",
        proposed_by_type="user", proposed_by_id="x", reason="Vor dem Update vorgeschlagen",
    )
    db_session.add(proposal)
    await db_session.commit()
    assert secret.owner_ext_id == "network"
    return proposal.id


async def _boot(db_session, test_settings) -> None:
    from nodvard_deck.main import app

    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    await extensions_service.load_enabled_from_registry(app, db_session, test_settings)
    await db_session.commit()


@pytest.mark.asyncio
async def test_update_from_0_8_moves_pihole_out_of_network(client, db_session, test_settings):
    proposal_id = await _installation_with_0_8(db_session, test_settings)
    await _boot(db_session, test_settings)
    db_session.expire_all()

    pihole = await db_session.get(ExtensionRecord, "pihole")
    assert pihole.state == "enabled", pihole.last_error
    assert pihole.settings == {"url": PIHOLE_URL + "/admin/", "tls_insecure_skip_verify": False}
    network = await db_session.get(ExtensionRecord, "network")
    assert network.settings == OLD_NETWORK, "die alte Zeile bleibt für den Rückweg unverändert"
    layout = (await db_session.execute(select(DashboardLayout))).scalar_one()
    assert [(i["ext_id"], i["widget_id"], i["x"]) for i in layout.items] == [("pihole", "status", 0), ("npm", "certificates", 2)]
    secret = (await db_session.execute(select(Secret).where(Secret.label == SECRET))).scalar_one()
    assert secret.owner_ext_id == "pihole"
    adopted = (await db_session.execute(
        select(AuditEntry).where(AuditEntry.action == "extension.adopted", AuditEntry.target_id == "pihole")
    )).scalar_one()
    assert adopted.detail == {
        "from": "network", "settings": True, "enabled": True, "widgets": 1, "secrets": 1, "extra_permissions": [],
    }
    assert "pihole" in get_extension_runtime().loaded and "network" in get_extension_runtime().loaded

    fake = FakePihole()
    _mock(fake)
    headers = await _login(client)
    body = (await client.get("/api/v1/ext/pihole/summary", headers=headers)).json()
    assert body["state"] == "ok", body
    settings = (await client.get("/api/v1/extensions/pihole/settings", headers=headers)).json()
    assert {s["label"]: s["is_set"] for s in settings["secrets"]} == {SECRET: True}
    npm_settings = (await client.get("/api/v1/extensions/npm/settings", headers=headers)).json()
    assert [s["label"] for s in npm_settings["secrets"]] == ["network-npm-password"]

    # Die alten Adressen gelten weiter (veraltet) und landen beim neuen Modul.
    old = (await client.get("/api/v1/ext/network/pihole", headers=headers)).json()
    assert old == body
    old_widget = (await client.get("/api/v1/ext/network/widgets/pihole", headers=headers)).json()
    assert old_widget["data"][0]["title"] == "Blockierung"
    assert (await client.post("/api/v1/ext/network/pihole/pause", json={"minutes": 7}, headers=headers)).status_code == 422
    paused = (await client.post("/api/v1/ext/network/pihole/pause", json={"minutes": 5}, headers=headers)).json()
    approved = await _approve(client, headers, paused["action_id"])
    assert (approved["action_type"], approved["status"]) == ("network.pihole_pause", "succeeded")
    from nodvard_deck.main import app

    app.openapi_schema = None
    assert app.openapi()["paths"]["/api/v1/ext/network/pihole"]["get"]["deprecated"] is True
    app.openapi_schema = None

    # Ein Vorschlag von vor dem Update führt jetzt das Modul „Pi-hole“ aus.
    approved = await _approve(client, headers, proposal_id)
    assert approved["status"] == "succeeded", approved
    assert approved["result"]["output"] == "Pi-hole-Blockierung für 5 Minuten pausiert."
    assert fake.changes.count("blocking {'blocking': False, 'timer': 300}") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(("state", "settings", "enabled"), [
    ("enabled", {"npm": OLD_NETWORK["npm"]}, False),
    ("disabled", OLD_NETWORK, False),
    ("enabled", {**OLD_NETWORK, "pihole": {"url": "", "tls_insecure_skip_verify": False}}, False),
])
async def test_pihole_only_starts_where_it_was_in_use(client, db_session, test_settings, state, settings, enabled):
    await _installation_with_0_8(db_session, test_settings, state=state, settings=settings)
    await _boot(db_session, test_settings)
    db_session.expire_all()
    pihole = await db_session.get(ExtensionRecord, "pihole")
    assert (pihole.state == "enabled") is enabled
    assert ("pihole" in get_extension_runtime().loaded) is enabled


@pytest.mark.asyncio
async def test_a_new_installation_starts_both_modules_off(client, db_session, test_settings):
    await _boot(db_session, test_settings)
    db_session.expire_all()
    assert (await db_session.get(ExtensionRecord, "pihole")).state == "disabled"
    assert (await db_session.get(ExtensionRecord, "network")).state == "disabled"
    assert (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "extension.adopted"))).scalars().all() == []
