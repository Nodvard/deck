"""`POST /extensions/{id}/test` (Verbindung testen), `needs_setup` in der Erweiterungsliste
und das Entfernen von Geheimnissen -- Einstellungs-Seite der Erweiterungen."""

from __future__ import annotations

import asyncio
from urllib.parse import quote, quote_plus
from types import SimpleNamespace

import pytest
from nodvard_sdk import HealthReport
from nodvard_sdk.capabilities import NotificationChannel
from nodvard_sdk.types import ConnectorHealth

from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import ExtensionRecord
from nodvard_deck.services import extension_test

SECRET = "super-geheimer-wert-4711"

SCHEMA = {
    "type": "object",
    "properties": {
        "base_url": {"type": "string", "title": "Adresse"},
        "topic": {"type": "string", "title": "Thema"},
        "tls_insecure_skip_verify": {"type": "boolean", "title": "Selbstsigniertes Zertifikat erlauben"},
    },
    "required": ["base_url", "topic"],
    "x-secrets": [
        {"label": "demo-pw", "title": "Passwort"},
        {"label": "demo-opt", "title": "Zusatzschlüssel", "optional": True},
    ],
}

GOOD = {"base_url": "https://nas.lan:8006", "topic": "t"}


class FakeExt:
    def __init__(self, report=None, exc=None, delay=0.0):
        self.report, self.exc, self.delay = report or HealthReport(healthy=True), exc, delay
        self.calls = 0

    async def health(self, ctx):  # noqa: ANN001
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.report


class FakeChannel:
    channel_id = "fake"
    label = "Fake"

    def __init__(self, send_exc=None, test_ok=True):
        self.send_exc, self.test_ok, self.sent = send_exc, test_ok, []

    async def send(self, notification):  # noqa: ANN001
        if self.send_exc:
            raise self.send_exc
        self.sent.append(notification)

    async def test(self):
        return ConnectorHealth(ok=self.test_ok, message=None if self.test_ok else "HTTP 500")


@pytest.fixture
def demo():
    runtime = get_extension_runtime()
    runtime.discovered["demo"] = SimpleNamespace(manifest=SimpleNamespace(settings_schema=SCHEMA), ok=True)
    yield runtime
    runtime.discovered.pop("demo", None)
    runtime.loaded.pop("demo", None)
    runtime.capabilities.clear_extension("demo")


def _load(runtime, instance):
    runtime.loaded["demo"] = SimpleNamespace(instance=instance, ctx=object(), settings_schema=None)


async def _owner(client):
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    r = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _role(client, db_session, role, username):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": "whatever123"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _record(db_session, state="enabled", settings=None):
    db_session.add(ExtensionRecord(id="demo", version="1", api_version="0.1.0", state=state, settings=settings if settings is not None else dict(GOOD)))
    await db_session.flush()


# -- Uebersetzung (rein) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("GET /version -> ConnectTimeout", "timeout"),
        ("ReadTimeout: timed out", "timeout"),
        ("GET /x -> HTTP 401: {\"errors\":1}", "auth"),
        ("PROPFIND / -> HTTP 403: Forbidden", "forbidden"),
        ("ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate", "tls"),
        ("ConnectError: [Errno -2] Name or service not known", "dns"),
        ("ConnectError: [Errno 111] Connection refused", "refused"),
        ("GET /x -> HTTP 404: nope", "not_found"),
        ("GET /x -> HTTP 502: bad gateway", "server_error"),
        ("SecretUnavailable: Secret 'x' existiert nicht.", "secret_missing"),
        ("Keine Verbindung konfiguriert.", "setup"),
        ("Der Server hat die Verbindung auf eine andere Adresse umgeleitet (HTTP 302). Prüfe …", "redirect"),
        ("HTTPStatusError: Redirect response '301 Moved Permanently' for url 'http://x' | HTTP 301", "redirect"),
        ("Die Antwort von Proxmox hat nicht das erwartete Format (HTTP 200). Stimmt die Adresse?", "invalid_answer"),
        ("völlig unbekannter Fehler", "unknown"),
    ],
)
def test_translate_kinds(text, kind):
    assert extension_test.translate(text).kind == kind


def test_translate_texts_are_german_and_helpful():
    host = "192.168.1.10:8006"
    assert extension_test.translate("ConnectTimeout", host=host).message == f"Keine Antwort von {host} – Adresse und Port prüfen."
    assert extension_test.translate("HTTP 401").message.startswith("Zugangsdaten abgelehnt")
    tls = extension_test.translate("SSLCertVerificationError", tls_hint="Selbstsigniertes Zertifikat erlauben").message
    assert tls.startswith("Zertifikat wird nicht vertraut") and "Selbstsigniertes Zertifikat erlauben" in tls
    assert extension_test.translate("ConnectError: Name or service not known").message.startswith("Adresse nicht gefunden")


@pytest.mark.parametrize("code", [300, 301, 302, 304, 307, 308, 399])
def test_translate_redirect_is_the_finished_sentence_without_location(code):
    """Eine Weiterleitung ergibt denselben Satz wie bei der Konsole, ohne den Vorspann
    „Technische Meldung“ und ohne die Zieladresse aus der rohen Meldung."""
    from nodvard_deck.ext.context import WebSocketRedirectRefused

    raw = (
        f"HTTPStatusError: Redirect response '{code} Found' for url 'http://192.168.2.10:8006/x' "
        f"Redirect location: 'https://192.168.2.99/andere-seite' | HTTP {code}"
    )
    t = extension_test.translate(raw)
    assert t.kind == "redirect"
    assert t.message == str(WebSocketRedirectRefused(code))
    assert "Technische Meldung" not in t.message and "andere-seite" not in t.message


def test_translate_invalid_answer_keeps_the_german_sentence():
    text = "Die Antwort von Proxmox hat nicht das erwartete Format (HTTP 200). Stimmt die Adresse?"
    t = extension_test.translate(text)
    assert t.kind == "invalid_answer"
    assert t.message == text


def test_scrub_removes_known_values_and_patterns():
    text = extension_test.scrub(f"GET https://bob:{SECRET}@host/x Authorization: Bearer abcdef123456 token={SECRET}", [SECRET])
    assert SECRET not in text and "abcdef123456" not in text and "bob:" not in text
    assert len(extension_test.scrub("x" * 1000)) <= 300


@pytest.mark.parametrize(
    "raw",
    [
        "GET /x?access_token=abc123def456 failed",
        "antwort: {\"token\": \"abc123def456\", \"x\": 1}",
        "{'password': 'abc123def456'}",
        "Cookie sessionid=abc123def456; other=1",
        "session: abc123def456",
        "refresh_token = abc123def456",
        "client_secret=abc123def456",
    ],
)
def test_scrub_patterns_review_cases(raw):
    assert "abc123def456" not in extension_test.scrub(raw)


def test_scrub_also_removes_url_encoded_secrets():
    secret = "p@ss w/ort&x=1"
    text = f"GET /x?pw={quote(secret, safe='')} und {quote_plus(secret)} und {secret}"
    out = extension_test.scrub(text, [secret])
    assert quote(secret, safe="") not in out and quote_plus(secret) not in out and secret not in out


def test_describe_exception_walks_cause_chain():
    try:
        try:
            raise OSError("[Errno -2] Name or service not known")
        except OSError as inner:
            raise RuntimeError("ConnectError") from inner
    except RuntimeError as exc:
        assert extension_test.translate(extension_test.describe_exception(exc)).kind == "dns"


# -- Endpunkt --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_ok(client, db_session, demo):
    await _record(db_session)
    _load(demo, FakeExt())
    headers = await _owner(client)
    r = await client.post("/api/v1/extensions/demo/test", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "message": "Verbindung funktioniert.", "details": None}


@pytest.mark.asyncio
async def test_test_translates_errors_with_host(client, db_session, demo):
    await _record(db_session)
    headers = await _owner(client)
    cases = [
        (HealthReport(healthy=False, message="GET /version -> ConnectTimeout"), "Keine Antwort von nas.lan:8006 – Adresse und Port prüfen."),
        (HealthReport(healthy=False, message="GET /version -> HTTP 401: x"), "Zugangsdaten abgelehnt"),
        (HealthReport(healthy=False, message="CERTIFICATE_VERIFY_FAILED"), "Selbstsigniertes Zertifikat erlauben"),
        (HealthReport(healthy=False, message="[Errno -2] Name or service not known"), "Adresse nicht gefunden"),
    ]
    for report, expected in cases:
        _load(demo, FakeExt(report=report))
        r = await client.post("/api/v1/extensions/demo/test", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is False
        assert expected in r.json()["message"], r.json()


@pytest.mark.asyncio
async def test_test_exception_from_health_is_reported_not_raised(client, db_session, demo):
    await _record(db_session)
    _load(demo, FakeExt(exc=RuntimeError("ConnectError: [Errno 111] Connection refused")))
    r = await client.post("/api/v1/extensions/demo/test", headers=await _owner(client))
    assert r.status_code == 200
    assert r.json()["ok"] is False and "nicht möglich" in r.json()["message"]


@pytest.mark.asyncio
async def test_test_timeout(client, db_session, demo, monkeypatch):
    monkeypatch.setattr(extension_test, "TEST_TIMEOUT_S", 0.05)
    await _record(db_session)
    _load(demo, FakeExt(delay=1))
    r = await client.post("/api/v1/extensions/demo/test", headers=await _owner(client))
    assert r.json()["ok"] is False and r.json()["message"].startswith("Keine Antwort von nas.lan:8006")


@pytest.mark.asyncio
async def test_test_never_leaks_secrets(client, db_session, demo):
    await _record(db_session)
    headers = await _owner(client)
    assert (await client.put("/api/v1/extensions/demo/secrets", json={"label": "demo-pw", "value": SECRET}, headers=headers)).status_code == 204
    leaky = f"seltsamer Fehler bei Anmeldung mit {SECRET} und Authorization: Bearer {SECRET}"
    _load(demo, FakeExt(report=HealthReport(healthy=False, message=leaky, details={"a": {"healthy": False, "error": leaky}})))
    r = await client.post("/api/v1/extensions/demo/test", headers=headers)
    assert r.status_code == 200
    assert SECRET not in r.text
    _load(demo, FakeExt(exc=RuntimeError(leaky)))
    r = await client.post("/api/v1/extensions/demo/test", headers=headers)
    assert SECRET not in r.text
    # auch nicht im Protokoll
    audit = await client.get("/api/v1/audit?action=extension.test", headers=headers)
    assert SECRET not in audit.text
    # und nicht im gemerkten letzten Test
    listing = await client.get("/api/v1/extensions", headers=headers)
    assert SECRET not in listing.text


@pytest.mark.asyncio
async def test_test_details_per_connection(client, db_session, demo):
    await _record(db_session, settings={"connections": [{"name": "pve1", "base_url": "https://10.0.0.1:8006"}, {"name": "pve2", "base_url": "https://10.0.0.2:8006"}], "base_url": "x", "topic": "y"})
    _load(demo, FakeExt(report=HealthReport(healthy=False, message="pve1: ok; pve2: FEHLER", details={
        "pve1": {"healthy": True, "version": "8"},
        "pve2": {"healthy": False, "error": "GET /version -> ConnectTimeout"},
    })))
    r = await client.post("/api/v1/extensions/demo/test", headers=await _owner(client))
    body = r.json()
    assert body["ok"] is False
    assert body["message"] == "1 von 2 Verbindungen funktionieren nicht – Einzelheiten unten."
    assert body["details"] == [
        {"name": "pve1", "ok": True, "message": "Verbindung funktioniert."},
        {"name": "pve2", "ok": False, "message": "Keine Antwort von 10.0.0.2:8006 – Adresse und Port prüfen."},
    ]


@pytest.mark.asyncio
async def test_test_disabled_extension_is_409_and_unknown_404(client, db_session, demo):
    await _record(db_session, state="disabled")
    headers = await _owner(client)
    r = await client.post("/api/v1/extensions/demo/test", headers=headers)
    assert r.status_code == 409 and "einschalten" in r.json()["detail"]
    assert (await client.post("/api/v1/extensions/nope/test", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_test_rate_limit(client, db_session, demo):
    await _record(db_session)
    fake = FakeExt()
    _load(demo, fake)
    headers = await _owner(client)
    for _ in range(10):
        assert (await client.post("/api/v1/extensions/demo/test", headers=headers)).status_code == 200
    r = await client.post("/api/v1/extensions/demo/test", headers=headers)
    assert r.status_code == 429 and "Minute" in r.json()["detail"]
    assert "Retry-After" in r.headers
    assert fake.calls == 10


@pytest.mark.asyncio
async def test_test_needs_permission(client, db_session, demo):
    await _record(db_session)
    _load(demo, FakeExt())
    assert (await client.post("/api/v1/extensions/demo/test")).status_code == 401
    await _owner(client)
    for role in ("operator", "viewer"):
        headers = await _role(client, db_session, role, f"u-{role}")
        assert (await client.post("/api/v1/extensions/demo/test", headers=headers)).status_code == 403, role


@pytest.mark.asyncio
async def test_test_writes_audit_entry(client, db_session, demo):
    await _record(db_session)
    _load(demo, FakeExt(report=HealthReport(healthy=False, message="HTTP 401")))
    headers = await _owner(client)
    await client.post("/api/v1/extensions/demo/test", headers=headers)
    audit = await client.get("/api/v1/audit?action=extension.test", headers=headers)
    rows = audit.json()["items"] if isinstance(audit.json(), dict) else audit.json()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "failure" and rows[0]["target_id"] == "demo"


@pytest.mark.asyncio
async def test_channel_test_is_part_of_connection_test_and_message_mode(client, db_session, demo):
    await _record(db_session)
    _load(demo, FakeExt())
    channel = FakeChannel(test_ok=False)
    demo.capabilities.provide("demo", NotificationChannel, channel)
    headers = await _owner(client)
    r = await client.post("/api/v1/extensions/demo/test", headers=headers)
    assert r.json()["ok"] is False and "HTTP 500" in r.json()["message"]

    channel.test_ok = True
    r = await client.post("/api/v1/extensions/demo/test", json={"mode": "message"}, headers=headers)
    assert r.json()["ok"] is True and "Testnachricht gesendet" in r.json()["message"]
    assert len(channel.sent) == 1
    assert "Nodvard Deck" in channel.sent[0].body

    channel.send_exc = RuntimeError("HTTP 403: forbidden")
    r = await client.post("/api/v1/extensions/demo/test", json={"mode": "message"}, headers=headers)
    assert r.json()["ok"] is False and r.json()["message"].startswith("Zugriff verweigert")


@pytest.mark.asyncio
async def test_message_mode_without_channel_is_422(client, db_session, demo):
    await _record(db_session)
    _load(demo, FakeExt())
    r = await client.post("/api/v1/extensions/demo/test", json={"mode": "message"}, headers=await _owner(client))
    assert r.status_code == 422


# -- needs_setup -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_needs_setup_lists_missing_fields_and_secrets(client, db_session, demo):
    await _record(db_session, settings={"base_url": "https://x"})
    headers = await _owner(client)
    row = next(e for e in (await client.get("/api/v1/extensions", headers=headers)).json() if e["id"] == "demo")
    assert row["needs_setup"] is True
    assert "„Thema“ ist noch nicht ausgefüllt." in row["setup_reasons"]
    assert "Zugangsdaten fehlen: Passwort." in row["setup_reasons"]
    assert not any("Zusatzschlüssel" in r for r in row["setup_reasons"])  # optional

    await client.put("/api/v1/extensions/demo/settings", json={"values": {"base_url": "https://x", "topic": "t"}}, headers=headers)
    await client.put("/api/v1/extensions/demo/secrets", json={"label": "demo-pw", "value": SECRET}, headers=headers)
    row = (await client.get("/api/v1/extensions/demo", headers=headers)).json()
    assert row["setup_reasons"] == [] and row["needs_setup"] is False


@pytest.mark.asyncio
async def test_needs_setup_only_for_enabled_extensions(client, db_session, demo):
    await _record(db_session, state="disabled", settings={})
    row = (await client.get("/api/v1/extensions/demo", headers=await _owner(client))).json()
    assert row["needs_setup"] is False


@pytest.mark.asyncio
async def test_needs_setup_after_failed_test_and_cleared_on_change(client, db_session, demo):
    await _record(db_session)
    await _owner(client)
    headers = await _owner_headers(client)
    await client.put("/api/v1/extensions/demo/secrets", json={"label": "demo-pw", "value": SECRET}, headers=headers)
    _load(demo, FakeExt(report=HealthReport(healthy=False, message="HTTP 401")))
    await client.post("/api/v1/extensions/demo/test", headers=headers)
    row = (await client.get("/api/v1/extensions/demo", headers=headers)).json()
    assert row["needs_setup"] is True
    assert row["last_test"]["ok"] is False
    assert row["setup_reasons"][-1].startswith("Der letzte Verbindungstest ist fehlgeschlagen: Zugangsdaten abgelehnt")

    # Zugangsdaten geaendert -> alter Fehlschlag ist hinfaellig
    await client.put("/api/v1/extensions/demo/secrets", json={"label": "demo-pw", "value": SECRET + "x"}, headers=headers)
    row = (await client.get("/api/v1/extensions/demo", headers=headers)).json()
    assert row["needs_setup"] is False and row["last_test"] is None

    _load(demo, FakeExt())
    await client.post("/api/v1/extensions/demo/test", headers=headers)
    assert (await client.get("/api/v1/extensions/demo", headers=headers)).json()["last_test"]["ok"] is True


async def _owner_headers(client):
    r = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_needs_setup_checks_required_fields_inside_list_items(db_session):
    from nodvard_deck.services import extension_setup

    schema = {
        "type": "object",
        "properties": {"connections": {"type": "array", "x-item-title": "Server", "items": {"type": "object", "properties": {"name": {}, "base_url": {"title": "Adresse"}}, "required": ["name", "base_url"]}}},
        "required": ["connections"],
        "x-secrets": [{"label": "tok:{name}", "per_item": "connections", "title": "Token"}],
    }
    assert extension_setup.setup_reasons(schema, {"connections": []}, set(), None) == ["Bei „connections“ fehlt noch ein Eintrag."]
    reasons = extension_setup.setup_reasons(schema, {"connections": [{"name": "pve1"}]}, set(), None)
    assert reasons == ["Server „pve1“: „Adresse“ fehlt.", "Zugangsdaten fehlen: Token (pve1)."]
    assert extension_setup.setup_reasons(schema, {"connections": [{"name": "pve1", "base_url": "https://x"}]}, {"tok:pve1"}, None) == []


# -- Geheimnisse entfernen ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_secret_can_be_removed_and_is_audited(client, db_session, demo):
    await _record(db_session)
    headers = await _owner(client)
    await client.put("/api/v1/extensions/demo/secrets", json={"label": "demo-pw", "value": SECRET}, headers=headers)
    slots = (await client.get("/api/v1/extensions/demo/settings", headers=headers)).json()["secrets"]
    assert [(s["label"], s["is_set"], s["optional"]) for s in slots] == [("demo-pw", True, False), ("demo-opt", False, True)]

    r = await client.delete("/api/v1/extensions/demo/secrets", params={"label": "demo-pw"}, headers=headers)
    assert r.status_code == 204, r.text
    slots = (await client.get("/api/v1/extensions/demo/settings", headers=headers)).json()["secrets"]
    assert slots[0]["is_set"] is False
    # idempotent
    assert (await client.delete("/api/v1/extensions/demo/secrets", params={"label": "demo-pw"}, headers=headers)).status_code == 204
    # fremdes Label
    assert (await client.delete("/api/v1/extensions/demo/secrets", params={"label": "proxmox-token:x"}, headers=headers)).status_code == 422
    audit = await client.get("/api/v1/audit?action=extension.secret_removed", headers=headers)
    assert SECRET not in audit.text


@pytest.mark.asyncio
async def test_secret_remove_needs_permission(client, db_session, demo):
    await _record(db_session)
    await _owner(client)
    assert (await client.delete("/api/v1/extensions/demo/secrets", params={"label": "demo-pw"})).status_code == 401
    viewer = await _role(client, db_session, "viewer", "gast")
    assert (await client.delete("/api/v1/extensions/demo/secrets", params={"label": "demo-pw"}, headers=viewer)).status_code == 403


@pytest.mark.asyncio
async def test_last_test_message_only_for_managers(client, db_session, demo):
    await _record(db_session)
    headers = await _owner(client)
    _load(demo, FakeExt(report=HealthReport(healthy=False, message="GET /x -> HTTP 401")))
    await client.post("/api/v1/extensions/demo/test", headers=headers)
    manager = (await client.get("/api/v1/extensions/demo", headers=headers)).json()
    assert manager["last_test"]["message"].startswith("Zugangsdaten abgelehnt")
    assert any("Zugangsdaten abgelehnt" in r for r in manager["setup_reasons"])

    viewer = await _role(client, db_session, "viewer", "gast")
    for url in ("/api/v1/extensions/demo", "/api/v1/extensions"):
        r = await client.get(url, headers=viewer)
        assert r.status_code == 200
        row = r.json() if isinstance(r.json(), dict) else next(e for e in r.json() if e["id"] == "demo")
        assert set(row["last_test"]) == {"ok", "at"} and row["last_test"]["ok"] is False
        assert row["needs_setup"] is True
        assert "Zugangsdaten abgelehnt" not in r.text
