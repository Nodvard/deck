"""Vaultwarden durch den echten Kern: Laden, Einstellungen, Zustand, Kachel, Health und App-Vorschlag. Vaultwarden selbst
ist ein Nachbau hinter `httpx.MockTransport`, mit Antworten im Format von `/alive`, `/api/config` und `/api/version`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import ExtensionRecord
from nodvard_deck.services import extensions as extensions_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vaultwarden"
URL = "https://vault.example.org"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeVaultwarden:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.insecure_requests: list[httpx.Request] = []
        self.config = _fixture("config.json")
        self.version: object = _fixture("version.json")
        self.answers: dict[str, httpx.Response] = {}
        self.error: Exception | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        path = request.url.path
        if path in self.answers:
            return self.answers[path]
        if path == "/alive":
            return httpx.Response(200, json=_fixture("alive.json"))
        if path == "/api/config":
            return httpx.Response(200, json=self.config)
        if path == "/api/version":
            return httpx.Response(200, json=self.version)
        return httpx.Response(404)

    def insecure_handler(self, request: httpx.Request) -> httpx.Response:
        self.insecure_requests.append(request)
        return self.handler(request)


async def _login(client) -> dict:
    account = {"username": "owner1", "password": "correct-horse-battery"}
    await client.post("/api/v1/auth/bootstrap", json={**account, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json=account)
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _enable(client, db_session, test_settings) -> tuple[dict, FakeVaultwarden]:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    headers = await _login(client)
    enabled = await client.post("/api/v1/extensions/vaultwarden/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text
    fake = FakeVaultwarden()
    http = get_extension_runtime().loaded["vaultwarden"].ctx.http
    # Zwei getrennte Nachbauten: so sieht ein Test, ob die Anfrage mit oder ohne Zertifikatsprüfung lief.
    http._client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    http._insecure_client = httpx.AsyncClient(transport=httpx.MockTransport(fake.insecure_handler))
    return headers, fake


async def _configure(client, headers, url: str = URL + "/#/login") -> None:
    saved = await client.put("/api/v1/extensions/vaultwarden/settings", json={"values": {"url": url}}, headers=headers)
    assert saved.status_code == 200, saved.text


async def _summary(client, headers) -> dict:
    res = await client.get("/api/v1/ext/vaultwarden/summary", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


async def _health(name: str = "vaultwarden"):
    loaded = get_extension_runtime().loaded[name]
    return await loaded.instance.health(loaded.ctx)


@pytest.mark.asyncio
async def test_vaultwarden_is_discovered_disabled_and_loads_cleanly(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    record = await db_session.get(ExtensionRecord, "vaultwarden")
    assert record is not None and record.state == "disabled"
    assert record.manifest["stage"] == "beta"

    headers, _ = await _enable(client, db_session, test_settings)
    assert record.state == "enabled", record.last_error
    pages = (await client.get("/api/v1/pages", headers=headers)).json()
    page = next(p for p in pages if p["ext_id"] == "vaultwarden")
    assert (page["path"], page["component"], page["nav_section"]) == ("/vaultwarden", "VaultwardenPage", "Sicherheit")
    # Im Menü „Sicherheit“ nach Nodvard Shield, nicht auf demselben Platz.
    assert page["nav_order"] == 30
    widgets = (await client.get("/api/v1/widgets", headers=headers)).json()
    assert {w["id"] for w in widgets if w["ext_id"] == "vaultwarden"} == {"status"}
    bundle = await client.get("/api/v1/extensions/vaultwarden/frontend/index.js")
    assert bundle.status_code == 200 and "VaultwardenPage" in bundle.text


@pytest.mark.asyncio
async def test_nothing_configured_is_reported_cleanly_everywhere(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    summary = await _summary(client, headers)
    assert summary["state"] == "not_configured" and "Erweiterungen → Vaultwarden" in summary["message"]
    widget = (await client.get("/api/v1/ext/vaultwarden/widgets/status", headers=headers)).json()
    assert widget["data"][0]["label"] == "nicht eingerichtet"
    health = await _health()
    assert health.healthy is False and "noch nicht eingerichtet" in health.message
    suggestions = (await client.get("/api/v1/apps/suggestions", headers=headers)).json()["suggestions"]
    assert all(s["source"] != "Vaultwarden" for s in suggestions)
    assert fake.requests == []


@pytest.mark.asyncio
async def test_a_running_vaultwarden_shows_version_https_and_closed_signups(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    summary = await _summary(client, headers)
    assert summary == {
        "state": "ok", "message": None, "url": URL, "https": True, "version": "1.32.0", "api_version": "2024.6.2",
        "server_name": "Vaultwarden", "signups_open": False, "domain": URL, "domain_matches": True,
    }
    assert [r.url.path for r in fake.requests] == ["/alive", "/api/config", "/api/version"]
    # Nur lesen und ohne Anmeldung: weder Passwort noch Token gehen an Vaultwarden.
    assert all(r.method == "GET" and "authorization" not in r.headers and "cookie" not in r.headers for r in fake.requests)

    rows = (await client.get("/api/v1/ext/vaultwarden/widgets/status", headers=headers)).json()["data"]
    assert [(r["title"], r["label"], r["tone"]) for r in rows] == [
        ("Vaultwarden", "läuft", "good"), ("Verbindung", "HTTPS", "good"), ("Registrierung", "zu", "good"),
    ]
    assert rows[0]["subtitle"] == "Läuft · Version 1.32.0"
    assert (await _health()).healthy is True
    suggestions = (await client.get("/api/v1/apps/suggestions", headers=headers)).json()["suggestions"]
    assert {"name": "Vaultwarden", "url": URL + "/", "source": "Vaultwarden"}.items() <= next(
        s for s in suggestions if s["source"] == "Vaultwarden"
    ).items()


@pytest.mark.asyncio
async def test_http_open_signups_and_another_domain_are_warnings(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, "192.0.2.20:8222")
    fake.config["settings"]["disableUserRegistration"] = False
    summary = await _summary(client, headers)
    assert (summary["url"], summary["https"], summary["signups_open"], summary["domain_matches"]) == (
        "http://192.0.2.20:8222", False, True, False,
    )
    rows = (await client.get("/api/v1/ext/vaultwarden/widgets/status", headers=headers)).json()["data"]
    assert [(r["label"], r["tone"]) for r in rows] == [("läuft", "good"), ("ohne HTTPS", "warn"), ("offen", "warn")]


@pytest.mark.asyncio
async def test_older_versions_without_the_new_fields_still_work(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    del fake.config["settings"]
    del fake.config["environment"]
    fake.answers["/api/version"] = httpx.Response(404)
    summary = await _summary(client, headers)
    assert summary["state"] == "ok"
    unknown = (summary["version"], summary["signups_open"], summary["domain"], summary["domain_matches"])
    assert unknown == (None, None, None, None)
    rows = (await client.get("/api/v1/ext/vaultwarden/widgets/status", headers=headers)).json()["data"]
    assert [r["title"] for r in rows] == ["Vaultwarden", "Verbindung"] and rows[0]["subtitle"] == "Läuft"


@pytest.mark.asyncio
async def test_odd_values_from_the_server_are_not_passed_on(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.version = "<script>alert(1)</script>"
    fake.config["server"]["name"] = "x" * 500
    fake.config["environment"]["vault"] = "javascript:alert(1)"
    fake.config["settings"]["disableUserRegistration"] = "nein"
    summary = await _summary(client, headers)
    dropped = (summary["version"], summary["server_name"], summary["domain"], summary["signups_open"])
    assert dropped == (None, None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(("setup", "message"), [
    (lambda f: setattr(f, "error", httpx.ConnectError("[Errno 111] Connection refused to 10.9.9.9")),
     "Vaultwarden ist nicht erreichbar. Prüfe die Adresse in den Einstellungen (http:// oder https://, Port)."),
    (lambda f: setattr(f, "error", httpx.ReadTimeout("timed out")), "Vaultwarden antwortet nicht (Zeitüberschreitung)."),
    (lambda f: setattr(f, "error", httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")), (
        "Vaultwarden: Das Zertifikat wurde nicht akzeptiert. Bei einem selbstsignierten Zertifikat in den Einstellungen "
        "„Selbstsigniertes Zertifikat erlauben“ einschalten."
    )),
    (lambda f: f.answers.update({"/alive": httpx.Response(404, text="<h1>nginx</h1>")}),
     "Unter der Adresse antwortet kein Vaultwarden (HTTP 404 auf /alive)."),
    (lambda f: f.answers.update({"/api/config": httpx.Response(200, text="<html>kein json</html>")}),
     "Unter der Adresse antwortet kein Vaultwarden (/api/config fehlt oder ist unlesbar)."),
])
async def test_problems_are_fixed_sentences_without_foreign_text(client, db_session, test_settings, setup, message):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    setup(fake)
    summary = await _summary(client, headers)
    assert (summary["state"], summary["message"]) == ("unreachable", message)
    assert "10.9.9.9" not in json.dumps(summary)
    rows = (await client.get("/api/v1/ext/vaultwarden/widgets/status", headers=headers)).json()["data"]
    assert [(r["label"], r["tone"], r["subtitle"]) for r in rows] == [("nicht erreichbar", "danger", message)]
    health = await _health()
    assert health.healthy is False and health.message == message


@pytest.mark.asyncio
async def test_a_redirect_is_never_followed_and_its_target_never_shown(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, "http://vault.example.org")
    fake.answers["/alive"] = httpx.Response(301, headers={"Location": "https://evil.example/steal"})
    summary = await _summary(client, headers)
    assert summary["state"] == "unreachable" and "HTTP 301" in summary["message"]
    assert "evil" not in summary["message"]
    assert [r.url.host for r in fake.requests] == ["vault.example.org"]


@pytest.mark.asyncio
async def test_a_broken_address_is_a_clear_hint(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, "ftp://vault.example.org")
    summary = await _summary(client, headers)
    assert summary["state"] == "not_configured" and "ist ungültig" in summary["message"]
    assert fake.requests == []


@pytest.mark.parametrize(
    "url", ["https://anna:geheim@vault.example.org", "anna:geheim@vault.example.org:8222", "https://anna@vault.example.org"],
)
def test_an_address_with_name_or_password_is_refused_without_repeating_it(url):
    # Ohne laufendes Modul fehlt der Paketpfad; die autouse-Fixture setzt sys.path danach zurück.
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "vaultwarden" / "src"))
    from nodvard_deck_ext_vaultwarden.config import (
        CredentialsInUrl,
        NotConfigured,
        normalize_url,
        vaultwarden_config,
    )

    with pytest.raises(CredentialsInUrl) as raised:
        normalize_url(url)
    assert "anna" not in str(raised.value) and "geheim" not in str(raised.value)
    with pytest.raises(NotConfigured) as refused:
        vaultwarden_config({"url": url})
    assert "Benutzernamen oder ein Passwort" in str(refused.value)
    assert "anna" not in str(refused.value) and "geheim" not in str(refused.value)


@pytest.mark.asyncio
async def test_a_password_in_the_address_shows_up_nowhere_and_nothing_is_asked(client, db_session, test_settings):
    """Die Adresse steht in der Übersicht, der Kachel und dem App-Vorschlag, die jeder mit Leserecht sieht."""
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers, "https://anna:geheim@vault.example.org")
    summary = await _summary(client, headers)
    assert summary["state"] == "not_configured" and "Benutzernamen oder ein Passwort" in summary["message"]
    widget = (await client.get("/api/v1/ext/vaultwarden/widgets/status", headers=headers)).json()
    suggestions = (await client.get("/api/v1/apps/suggestions", headers=headers)).json()["suggestions"]
    health = await _health()
    for shown in (json.dumps(summary), json.dumps(widget), json.dumps(suggestions), health.message):
        assert "geheim" not in shown and "anna" not in shown
    assert fake.requests == [] and fake.insecure_requests == []


@pytest.mark.asyncio
async def test_certificates_are_checked_unless_the_exception_is_switched_on(client, db_session, test_settings):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    assert (await _summary(client, headers))["state"] == "ok"
    assert fake.requests and fake.insecure_requests == [], "ohne Einstellung immer mit Zertifikatsprüfung"

    saved = await client.put(
        "/api/v1/extensions/vaultwarden/settings",
        json={"values": {"url": URL, "tls_insecure_skip_verify": True}}, headers=headers,
    )
    assert saved.status_code == 200, saved.text
    fake.requests.clear()
    assert (await _summary(client, headers))["state"] == "ok"
    assert fake.insecure_requests and len(fake.requests) == len(fake.insecure_requests), "nur über den Client ohne Prüfung"


@pytest.mark.asyncio
async def test_the_routes_need_a_login(client, db_session, test_settings):
    await _enable(client, db_session, test_settings)
    for path in ("summary", "widgets/status"):
        assert (await client.get(f"/api/v1/ext/vaultwarden/{path}")).status_code == 401


def test_every_page_in_the_security_menu_has_its_own_place():
    """Zwei Seiten mit derselben `nav_order` im selben Abschnitt stehen je nach Ladereihenfolge mal so, mal so."""
    import re

    orders: dict[int, list[str]] = {}
    for init in sorted(REPO_EXTENSIONS_DIR.glob("*/src/*/__init__.py")):
        for order in re.findall(r'nav_section="Sicherheit",\s*nav_order=(\d+)', init.read_text(encoding="utf-8")):
            orders.setdefault(int(order), []).append(init.parts[-4])
    assert orders and all(len(owners) == 1 for owners in orders.values()), orders
