"""Nginx Proxy Manager: der Client gegen `httpx.MockTransport`, ohne echten Server. Geprüft werden Anmeldung,
zwischengespeichertes Token, erneute Anmeldung nach 401, das Auslesen der Antworten und verständliche Fehler."""

from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from raw_http_helpers import (
    BROKEN_ANSWERS,
    REDIRECT_ANSWERS,
    raw_http_server,
    whole_chain,
)

EXT_SRC = Path(__file__).resolve().parents[2] / "extensions" / "npm" / "src"
_NAMES = {
    "config": ("NotConfigured", "normalize_url", "npm_config"),
    "client": ("NpmAuthError", "NpmClient", "NpmError", "certificate_state", "days_text", "parse_datetime"),
}


# Gesetzt von `_ext_npm_on_path` je Test: Das Modul ist kein installiertes Paket und wird nicht beim Sammeln importiert.
NotConfigured: Any = None
normalize_url: Any = None
npm_config: Any = None
NpmAuthError: Any = None
NpmClient: Any = None
NpmError: Any = None
certificate_state: Any = None
days_text: Any = None
parse_datetime: Any = None


@pytest.fixture(autouse=True)
def _ext_npm_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_SRC))
    for module, names in _NAMES.items():
        loaded = importlib.import_module(f"nodvard_deck_ext_npm.{module}")
        for name in names:
            monkeypatch.setitem(globals(), name, getattr(loaded, name))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_npm"):
            del sys.modules[name]


NPM = "http://npm.test:81"

EVIL_LOCATION = "https://angreifer.example/login?x=geheim"


def _assert_redirect_text(text: str, service: str, code: int) -> None:
    assert text.startswith(f"{service} leitet auf eine andere Adresse um (HTTP {code}).")
    assert "endgültige Adresse" in text
    # Der Location-Header kommt vom fremden Server und darf nirgends im Text stehen.
    assert "angreifer" not in text.lower()
    assert "geheim" not in text


class FakeHttp:
    """Steht fuer `ctx.http`: gleiche `request()`-Signatur (inkl. `insecure_tls`)."""

    def __init__(self, handler) -> None:
        self.requests: list[httpx.Request] = []
        self.insecure: list[bool] = []

        def _record(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        self._client = httpx.AsyncClient(transport=httpx.MockTransport(_record))

    async def request(self, method: str, url: str, *, insecure_tls: bool = False, **kwargs):
        self.insecure.append(insecure_tls)
        return await self._client.request(method, url, **kwargs)

    def paths(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]


class Password:
    def __init__(self, value: str | None) -> None:
        self.value = value
        self.calls = 0

    async def __call__(self) -> str | None:
        self.calls += 1
        return self.value


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content) if request.content else {}


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------


def test_normalize_url_adds_scheme_and_strips_the_api_suffix():
    assert normalize_url("192.0.2.2", strip_suffixes=("/api",)) == "http://192.0.2.2"
    assert normalize_url("http://npm.example:81/api/", strip_suffixes=("/api",)) == "http://npm.example:81"
    assert normalize_url("  ", strip_suffixes=()) is None
    with pytest.raises(ValueError):
        normalize_url("ftp://pi.hole")


@pytest.mark.parametrize(("raw", "expected"), [
    ("http://npm.example:81/nginx/proxy", "http://npm.example:81"),
    ("http://npm.example:81/nginx/proxy/", "http://npm.example:81"),
    ("http://npm.example:81/nginx/certificates", "http://npm.example:81"),
    ("http://npm.example:81/certificates", "http://npm.example:81"),
    ("http://npm.example:81/access", "http://npm.example:81"),
    ("http://npm.example:81/logs", "http://npm.example:81"),
    ("https://example.org/npm/certificates/", "https://example.org/npm"),
    ("http://npm.example:81/nginx/redirection", "http://npm.example:81"),
    ("http://npm.example:81/Nginx/Stream", "http://npm.example:81"),
    ("192.0.2.2:81/users", "http://192.0.2.2:81"),
    ("http://npm.example:81/audit-log", "http://npm.example:81"),
    ("http://npm.example:81/settings", "http://npm.example:81"),
    ("http://npm.example:81/login?next=%2F#x", "http://npm.example:81"),
    ("https://example.org/npm/nginx/proxy", "https://example.org/npm"),
    ("https://example.org/npm/users", "https://example.org/npm"),
    ("http://host/npm", "http://host/npm"),
    ("http://host/npm/", "http://host/npm"),
    ("http://host/nginx", "http://host/nginx"),
    ("http://host/nginx/", "http://host/nginx"),
    ("http://host/nginx/api", "http://host/nginx"),
    ("http://host/users-verwaltung", "http://host/users-verwaltung"),
])
def test_a_page_copied_from_the_browser_becomes_the_base_address(raw, expected):
    assert normalize_url(raw, strip_suffixes=("/api",)) == expected
    assert npm_config({"url": raw, "identity": "admin@example.com"}).url == expected


def test_config_reports_missing_parts_as_friendly_not_configured():
    with pytest.raises(NotConfigured, match="noch nicht eingerichtet"):
        npm_config({})
    with pytest.raises(NotConfigured, match="ungültig"):
        npm_config({"url": "ftp://x"})
    with pytest.raises(NotConfigured, match="E-Mail-Adresse"):
        npm_config({"url": "http://npm:81"})
    cfg = npm_config({"url": "http://npm:81/api/", "identity": " admin@example.com "})
    assert (cfg.url, cfg.identity, cfg.notify_expiring) == ("http://npm:81", "admin@example.com", True)


# ---------------------------------------------------------------------------
# Nginx Proxy Manager
# ---------------------------------------------------------------------------

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

CERTS = [
    {"id": 1, "provider": "letsencrypt", "nice_name": "cloud.home.example", "domain_names": ["cloud.home.example"], "expires_on": "2026-12-01T10:00:00.000Z"},
    {"id": 2, "provider": "letsencrypt", "nice_name": "", "domain_names": ["nas.home.example"], "expires_on": "2026-10-04 08:00:00"},
    {"id": 3, "provider": "other", "nice_name": "Altes Zertifikat", "domain_names": ["alt.home.example"], "expires_on": "2026-09-20T00:00:00Z"},
    {"id": 4, "provider": "letsencrypt", "domain_names": ["x.home.example"], "expires_on": None},
]

HOSTS = [
    {"id": 7, "domain_names": ["cloud.home.example"], "forward_scheme": "http", "forward_host": "192.168.1.85", "forward_port": 8080,
     "certificate_id": 1, "ssl_forced": True, "enabled": True, "meta": {"nginx_online": True, "nginx_err": None}},
    {"id": 8, "domain_names": ["nas.home.example"], "forward_scheme": "https", "forward_host": "192.168.1.10", "forward_port": 5001,
     "certificate_id": 2, "ssl_forced": 1, "enabled": 0, "meta": {}},
    {"id": 9, "domain_names": ["intern.home.example"], "forward_host": "10.0.0.5", "forward_port": 80, "certificate_id": 0, "ssl_forced": 0, "enabled": 1},
]


class FakeNpm:
    def __init__(self, *, identity: str = "admin@example.com", secret: str = "npm-geheim", expires: str = "2026-09-30T12:00:00.000Z") -> None:
        self.identity, self.secret, self.expires = identity, secret, expires
        self.tokens: set[str] = set()
        self.counter = 0
        self.hosts = [dict(h) for h in HOSTS]
        self.require_2fa = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/tokens" and request.method == "POST":
            body = _body(request)
            if (body.get("identity"), body.get("secret")) != (self.identity, self.secret):
                return httpx.Response(401, json={"error": {"code": 401, "message": "Invalid email or password"}})
            if self.require_2fa:
                return httpx.Response(200, json={"requires_2fa": True, "challenge_token": "c"})
            self.counter += 1
            token = f"jwt-{self.counter}"
            self.tokens.add(token)
            return httpx.Response(200, json={"token": token, "expires": self.expires})
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] not in self.tokens:
            return httpx.Response(401, json={"error": {"code": 401, "message": "Unauthorized"}})
        if path == "/api/nginx/certificates":
            return httpx.Response(200, json=CERTS)
        if path == "/api/nginx/proxy-hosts":
            return httpx.Response(200, json=self.hosts)
        for host in self.hosts:
            for verb, value in (("enable", True), ("disable", False)):
                if path == f"/api/nginx/proxy-hosts/{host['id']}/{verb}" and request.method == "POST":
                    if bool(host["enabled"]) == value:
                        return httpx.Response(400, json={"error": {"code": 400, "message": f"Host is already {verb}d"}})
                    host["enabled"] = value
                    return httpx.Response(200, json=True)
        return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _npm(fake, password: Password | None = None, clock: Clock | None = None) -> tuple[NpmClient, FakeHttp, Password]:
    http = FakeHttp(fake)
    password = password or Password("npm-geheim")
    client = NpmClient(http, base_url=NPM, identity="admin@example.com", password=password, clock=clock or Clock(NOW.timestamp()))
    return client, http, password


def test_certificate_state_and_texts():
    exp = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    assert certificate_state(exp, NOW) == ("warn", 6)
    assert certificate_state(datetime(2026, 12, 1, tzinfo=timezone.utc), NOW)[0] == "ok"
    assert certificate_state(datetime(2026, 9, 27, tzinfo=timezone.utc), NOW) == ("expired", -3)
    assert certificate_state(None, NOW) == ("unknown", None)
    assert days_text("warn", 6) == "noch 6 Tage"
    assert days_text("warn", 1) == "noch 1 Tag"
    assert days_text("warn", 0) == "läuft heute ab"
    assert days_text("expired", -3) == "seit 2 Tagen abgelaufen"
    assert days_text("expired", -1) == "heute abgelaufen"
    assert parse_datetime("2026-10-04 08:00:00") == datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)
    assert parse_datetime("kaputt") is None


@pytest.mark.asyncio
async def test_npm_lists_hosts_with_target_and_certificate_expiry():
    client, http, password = _npm(FakeNpm())

    certs = await client.certificates(now=NOW)
    hosts = await client.proxy_hosts(certs)

    by_id = {c["id"]: c for c in certs}
    assert by_id[1]["status"] == "ok"
    assert (by_id[2]["status"], by_id[2]["days_left"], by_id[2]["name"]) == ("warn", 4, "nas.home.example")
    assert (by_id[3]["status"], by_id[3]["status_label"], by_id[3]["provider_label"]) == ("expired", "abgelaufen", "eigenes Zertifikat")
    assert by_id[4]["status"] == "unknown"

    hosts_by_id = {h["id"]: h for h in hosts}
    assert hosts_by_id[7]["target"] == "http://192.168.1.85:8080"
    assert hosts_by_id[7]["certificate"]["id"] == 1
    assert hosts_by_id[7]["nginx_online"] is True
    assert (hosts_by_id[8]["enabled"], hosts_by_id[8]["ssl_forced"], hosts_by_id[8]["target"]) == (False, True, "https://192.168.1.10:5001")
    assert (hosts_by_id[9]["certificate_id"], hosts_by_id[9]["certificate"], hosts_by_id[9]["enabled"]) == (None, None, True)

    assert http.paths() == ["POST /api/tokens", "GET /api/nginx/certificates", "GET /api/nginx/proxy-hosts"]
    assert _body(http.requests[0]) == {"identity": "admin@example.com", "secret": "npm-geheim"}
    assert http.requests[1].headers["Authorization"] == "Bearer jwt-1"
    assert password.calls == 1


@pytest.mark.asyncio
async def test_npm_renews_the_token_before_it_expires_and_after_a_401():
    fake = FakeNpm(expires="2026-09-29T13:00:00Z")
    clock = Clock(NOW.timestamp())
    client, http, password = _npm(fake, clock=clock)
    await client.certificates(now=NOW)

    clock.now += 50 * 60  # noch 10 Minuten gueltig -> wiederverwenden
    await client.certificates(now=NOW)
    assert http.paths().count("POST /api/tokens") == 1

    clock.now += 9 * 60  # weniger als 2 Minuten Rest -> vorher erneuern
    fake.expires = "2026-09-30T12:00:00Z"
    await client.certificates(now=NOW)
    assert http.paths().count("POST /api/tokens") == 2

    fake.tokens.clear()  # z. B. NPM neu gestartet
    await client.proxy_hosts()
    assert http.paths()[-3:] == ["GET /api/nginx/proxy-hosts", "POST /api/tokens", "GET /api/nginx/proxy-hosts"]
    assert http.requests[-1].headers["Authorization"] == "Bearer jwt-3"
    assert password.calls == 3


@pytest.mark.asyncio
async def test_npm_wrong_credentials_and_two_factor_are_explained():
    client, http, _ = _npm(FakeNpm(), Password("falsch"))
    with pytest.raises(NpmAuthError, match="Anmeldung abgelehnt") as exc:
        await client.certificates()
    assert exc.value.state == "auth_failed"
    with pytest.raises(NpmAuthError):
        await client.proxy_hosts()
    assert http.paths() == ["POST /api/tokens"]

    fake = FakeNpm()
    fake.require_2fa = True
    client, _, _ = _npm(fake)
    with pytest.raises(NpmAuthError, match="Zwei-Faktor"):
        await client.certificates()


@pytest.mark.asyncio
async def test_npm_wrong_address_and_unreachable():
    client, _, _ = _npm(lambda request: httpx.Response(404, text="<html>not found</html>"))
    with pytest.raises(NpmError, match="Port 81"):
        await client.certificates()

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 113] No route to host")

    client, _, _ = _npm(boom)
    with pytest.raises(NpmError, match="Nginx Proxy Manager ist nicht erreichbar") as exc:
        await client.certificates()
    assert exc.value.state == "unreachable"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
async def test_npm_login_redirect_never_names_the_foreign_address_and_is_not_followed(code):
    client, http, _ = _npm(lambda request: httpx.Response(code, headers={"Location": EVIL_LOCATION}))
    with pytest.raises(NpmError) as exc:
        await client.certificates()
    _assert_redirect_text(str(exc.value), "Nginx Proxy Manager", code)
    assert http.paths() == ["POST /api/tokens"]


@pytest.mark.asyncio
@pytest.mark.parametrize("call", ["certificates", "proxy_hosts", "enable_host"])
async def test_npm_redirect_after_login_is_an_error_even_with_a_json_body(call):
    """Eine 3xx-Antwort auf einen Abruf nach der Anmeldung ist kein Erfolg -- auch nicht,
    wenn der Koerper wie eine Liste oder `true` aussieht."""
    fake = FakeNpm()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/nginx/"):
            return httpx.Response(302, headers={"Location": EVIL_LOCATION}, json=True if request.method == "POST" else [])
        return fake(request)

    client, http, _ = _npm(handler)
    with pytest.raises(NpmError) as exc:
        if call == "certificates":
            await client.certificates()
        elif call == "proxy_hosts":
            await client.proxy_hosts()
        else:
            await client.set_host_enabled(7, True)
    _assert_redirect_text(str(exc.value), "Nginx Proxy Manager", 302)
    assert not any(r.url.host == "angreifer.example" for r in http.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location", ["javascript:alert(1)", "http://npm.test:angreifer-text/", "http://xn--angreifer-ey9f.example/login"]
)
async def test_npm_unusable_location_is_no_crash_and_never_in_the_text(location):
    client, http, _ = _npm(lambda request: httpx.Response(301, headers={"Location": location}))
    with pytest.raises(NpmError) as exc:
        await client.certificates()
    text = str(exc.value)
    assert text.startswith("Nginx Proxy Manager leitet auf eine andere Adresse um")
    chain = whole_chain(exc.value)
    assert "angreifer" not in chain.lower() and "alert" not in chain
    assert http.paths() == ["POST /api/tokens"]


class RealHttp:
    """Steht fuer `ctx.http`, aber mit einem echten httpx-Client (samt h11) und ohne Proxy aus der Umgebung."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(trust_env=False)

    async def request(self, method: str, url: str, *, insecure_tls: bool = False, **kwargs):
        return await self._client.request(method, url, **kwargs)


RAW_CASES = [(name, "leitet auf eine andere Adresse um.") for name in REDIRECT_ANSWERS] + [
    (name, "hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt.") for name in BROKEN_ANSWERS
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("answer", "sentence"), RAW_CASES)
async def test_broken_answers_of_the_real_parser_never_reach_a_text(answer, sentence):
    """Kaputte Kopfzeilen scheitern schon in h11, und dessen Text zitiert die Zeile (auch einen
    `Location`-Header). Der Satz ist fest, die Ursachenkette leer."""
    answers = {**REDIRECT_ANSWERS, **BROKEN_ANSWERS}
    async with raw_http_server(answers[answer]) as base:
        client = NpmClient(RealHttp(), base_url=base, identity="admin@example.com", password=Password("geheim"))
        with pytest.raises(NpmError) as exc:
            await client.certificates()
    assert str(exc.value).startswith(f"Nginx Proxy Manager {sentence}")
    assert exc.value.state == "unreachable"
    chain = whole_chain(exc.value)
    assert "angreifer" not in chain.lower() and "bytearray" not in chain


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "sentence"),
    [
        (httpx.LocalProtocolError("Illegal header value b'jwt-geheim\\n'"), "Die Anfrage an Nginx Proxy Manager ließ sich nicht senden."),
        (httpx.RemoteProtocolError("Server disconnected without sending a response."), "Nginx Proxy Manager hat die Verbindung abgebrochen"),
        (httpx.RemoteProtocolError("illegal header line: bytearray(b'Location: https://angreifer.example/')"), "Nginx Proxy Manager leitet auf eine andere Adresse um."),
    ],
)
async def test_protocol_errors_get_a_fixed_sentence_without_their_text(raised, sentence):
    """Bei der eigenen Anfrage nennt h11 den kaputten Header samt Wert (hier: das Token)."""

    def boom(request: httpx.Request) -> httpx.Response:
        raise raised

    client, _, _ = _npm(boom)
    with pytest.raises(NpmError) as exc:
        await client.certificates()
    assert str(exc.value).startswith(sentence)
    chain = whole_chain(exc.value)
    assert "geheim" not in chain and "angreifer" not in chain and "disconnected" not in chain


@pytest.mark.asyncio
async def test_npm_deeply_nested_json_is_an_error_not_a_crash():
    fake = FakeNpm()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/nginx/certificates":
            return httpx.Response(200, content=b"[" * 200_000 + b"]" * 200_000, headers={"Content-Type": "application/json"})
        return fake(request)

    client, _, _ = _npm(handler)
    with pytest.raises(NpmError, match="keine gültige Antwort"):
        await client.certificates()


@pytest.mark.asyncio
async def test_npm_enable_disable_host():
    fake = FakeNpm()
    client, http, _ = _npm(fake)

    assert await client.set_host_enabled(7, False) is True
    assert http.paths()[-1] == "POST /api/nginx/proxy-hosts/7/disable"
    assert fake.hosts[0]["enabled"] is False
    assert await client.set_host_enabled(7, False) is False, "schon aus -> keine Aenderung, kein Fehler"
    assert await client.set_host_enabled(8, True) is True
    with pytest.raises(NpmError, match="gibt es im Nginx Proxy Manager nicht"):
        await client.set_host_enabled(99, True)


@pytest.mark.asyncio
async def test_npm_missing_password_is_an_auth_error_without_a_request():
    client, http, _ = _npm(FakeNpm(), Password(None))
    with pytest.raises(NpmAuthError, match="fehlt noch das Passwort"):
        await client.certificates()
    assert http.requests == []
