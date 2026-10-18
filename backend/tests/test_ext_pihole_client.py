"""Pi-hole: Client gegen `httpx.MockTransport` mit Antworten im Format von Pi-hole v6 (`fixtures/pihole`)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest
from raw_http_helpers import (
    BROKEN_ANSWERS,
    REDIRECT_ANSWERS,
    raw_http_server,
    whole_chain,
)

REPO = Path(__file__).resolve().parents[2]
EXT_SRC = REPO / "extensions" / "pihole" / "src"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pihole"
PIHOLE = "http://pihole.test"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _ext_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_SRC))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_pihole"):
            del sys.modules[name]


def _client_mod():
    return importlib.import_module("nodvard_deck_ext_pihole.client")


def _config_mod():
    return importlib.import_module("nodvard_deck_ext_pihole.config")


class FakeHttp:
    """Steht für `ctx.http`: gleiche `request()`-Signatur (mit `insecure_tls`)."""

    def __init__(self, handler) -> None:
        self.requests: list[httpx.Request] = []
        self.insecure: list[bool] = []
        self.timeouts: list[float | None] = []

        def _record(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        self._client = httpx.AsyncClient(transport=httpx.MockTransport(_record))

    async def request(self, method: str, url: str, *, insecure_tls: bool = False, **kwargs):
        self.insecure.append(insecure_tls)
        self.timeouts.append(kwargs.get("timeout"))
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


class FakePihole:
    """Pi-hole v6 im Kleinen: Passwort prüfen, Sitzungen vergeben und entwerten, Antworten aus `fixtures/pihole`."""

    def __init__(self, password: str | None = "geheim") -> None:
        self.password = password
        self.sessions: set[str] = set()
        self.counter = 0
        self.blocking = {"blocking": "enabled", "timer": None}
        self.posted: list[dict] = []
        self.domains: dict[str, set[str]] = {"allow": set(), "deny": set()}

    def expire_all(self) -> None:
        self.sessions.clear()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth" and request.method == "POST":
            if self.password is None:
                return httpx.Response(200, json={"session": {"valid": True, "totp": False, "sid": None, "validity": -1}})
            if _body(request).get("password") != self.password:
                return httpx.Response(401, json={"error": {"key": "unauthorized", "message": "Unauthorized", "hint": None}})
            self.counter += 1
            sid = f"sid-{self.counter}"
            self.sessions.add(sid)
            answer = _fixture("auth.json")
            answer["session"]["sid"] = sid
            return httpx.Response(200, json=answer)
        if path == "/api/auth" and request.method == "DELETE":
            self.sessions.discard(request.headers.get("X-FTL-SID", ""))
            return httpx.Response(204)
        if self.password is not None and request.headers.get("X-FTL-SID") not in self.sessions:
            return httpx.Response(401, json={"error": {"key": "unauthorized", "message": "Unauthorized"}})
        if path == "/api/stats/summary":
            return httpx.Response(200, json=_fixture("stats_summary.json"))
        if path == "/api/stats/top_clients":
            return httpx.Response(200, json=_fixture("top_clients.json"))
        if path == "/api/dns/blocking" and request.method == "GET":
            return httpx.Response(200, json={**self.blocking, "took": 0.0})
        if path == "/api/dns/blocking" and request.method == "POST":
            body = _body(request)
            self.posted.append(body)
            self.blocking = {"blocking": "enabled" if body["blocking"] else "disabled", "timer": body.get("timer")}
            return httpx.Response(200, json={**self.blocking, "took": 0.0})
        if path == "/api/action/gravity" and request.method == "POST":
            return httpx.Response(200, content=(FIXTURES / "gravity.txt").read_bytes(), headers={"Content-Type": "text/plain"})
        for kind in ("allow", "deny"):
            if path == f"/api/domains/{kind}/exact" and request.method == "POST":
                body = _body(request)
                self.posted.append({"list": kind, **body})
                if body["domain"] in self.domains[kind]:
                    return httpx.Response(201, json=_fixture("domains_duplicate.json"))
                self.domains[kind].add(body["domain"])
                return httpx.Response(201, json=_fixture("domains_added.json"))
        return httpx.Response(404, text="Not found")


def _pihole(fake, password: Password | None = None, **kwargs):
    http = FakeHttp(fake)
    password = password or Password("geheim")
    return _client_mod().PiholeClient(http, base_url=PIHOLE, password=password, **kwargs), http, password


def test_config_is_flat_and_strips_admin_and_api():
    config = _config_mod()
    assert config.pihole_config({"url": "https://pi.hole/admin/"}).url == "https://pi.hole"
    assert config.pihole_config({"url": "192.0.2.2/api", "tls_insecure_skip_verify": True}) == config.PiholeConfig(
        url="http://192.0.2.2", insecure_tls=True,
    )
    with pytest.raises(config.NotConfigured, match="Einstellungen → Erweiterungen → Pi-hole"):
        config.pihole_config({})
    with pytest.raises(config.NotConfigured, match="ungültig"):
        config.pihole_config({"url": "ftp://x"})
    # Der alte Abschnitt aus „Netzwerk“ gilt hier nicht: Die Übernahme kopiert ihn flach.
    with pytest.raises(config.NotConfigured):
        config.pihole_config({"pihole": {"url": "http://pi.hole"}})


@pytest.mark.parametrize(("raw", "expected"), [
    ("http://pi.hole/admin/queries", "http://pi.hole"),
    ("http://pi.hole/admin/settings/dns/", "http://pi.hole"),
    ("http://pi.hole/Admin/groups", "http://pi.hole"),
    ("http://pi.hole/admin/login?redirect=%2Fqueries#x", "http://pi.hole"),
    ("192.0.2.2:8080/admin/queries", "http://192.0.2.2:8080"),
    ("http://pi.hole/api/docs", "http://pi.hole"),
    ("https://example.org/pihole/admin/queries", "https://example.org/pihole"),
    ("https://example.org/pihole/admin", "https://example.org/pihole"),
    ("http://host/pihole", "http://host/pihole"),
    ("http://host/pihole/", "http://host/pihole"),
    ("http://host/administration", "http://host/administration"),
    ("http://host/pihole/api", "http://host/pihole"),
    ("http://host/api/pihole", "http://host/api/pihole"),
])
def test_a_page_copied_from_the_browser_becomes_the_base_address(raw, expected):
    assert _config_mod().pihole_config({"url": raw}).url == expected


@pytest.mark.parametrize(("raw", "domain"), [
    ("werbung.example.com", "werbung.example.com"),
    ("  Werbung.Example.COM. ", "werbung.example.com"),
    ("a-b.c0.example", "a-b.c0.example"),
])
def test_domains_are_normalized(raw, domain):
    assert _config_mod().normalize_domain(raw) == domain


@pytest.mark.parametrize("raw", [
    None, 5, "", "localhost", "http://werbung.example.com", "werbung.example.com/pfad", "*.example.com",
    "-a.example.com", "a-.example.com", "a..example.com", "a b.example.com", "ä.example.com", "x" * 64 + ".com",
    ("a" * 60 + ".") * 5 + "com",
])
def test_odd_domains_are_refused(raw):
    with pytest.raises(ValueError, match="Bitte eine Domain"):
        _config_mod().normalize_domain(raw)


@pytest.mark.asyncio
async def test_logs_in_once_and_reuses_the_session():
    client, http, password = _pihole(FakePihole())

    summary = await client.summary()
    blocking = await client.blocking()
    clients = await client.top_clients()

    assert summary == {
        "queries_total": 29160, "queries_blocked": 6379, "percent_blocked": 21.9, "domains_blocked": 104642,
        "gravity_updated_at": 1791100800, "clients_active": 14,
    }
    assert blocking == {"status": "enabled", "enabled": True, "timer_s": None}
    assert clients[:2] == [
        {"name": "fernseher.lan", "ip": "192.0.2.44", "count": 5896},
        {"name": "192.0.2.12", "ip": "192.0.2.12", "count": 4120},
    ]
    assert len(clients) == 5
    assert http.paths() == ["POST /api/auth", "GET /api/stats/summary", "GET /api/dns/blocking", "GET /api/stats/top_clients"]
    assert http.requests[-1].url.params["count"] == "5"
    assert password.calls == 1, "das Passwort kommt nur zur Anmeldung aus dem Tresor"
    assert _body(http.requests[0]) == {"password": "geheim"}
    assert all(r.headers.get("X-FTL-SID") == "sid-1" for r in http.requests[1:])


@pytest.mark.asyncio
async def test_logs_in_again_after_the_session_expired():
    fake = FakePihole()
    client, http, password = _pihole(fake)
    await client.summary()

    fake.expire_all()
    assert (await client.summary())["queries_total"] == 29160
    assert http.paths()[-3:] == ["GET /api/stats/summary", "POST /api/auth", "GET /api/stats/summary"]
    assert http.requests[-1].headers["X-FTL-SID"] == "sid-2"
    assert password.calls == 2


@pytest.mark.asyncio
async def test_wrong_password_is_reported_and_not_hammered():
    client, http, _ = _pihole(FakePihole(password="richtig"), Password("falsch"))
    with pytest.raises(_client_mod().PiholeAuthError, match="Passwort abgelehnt") as first:
        await client.summary()
    assert first.value.state == "auth_failed"
    with pytest.raises(_client_mod().PiholeAuthError):
        await client.blocking()
    assert http.paths() == ["POST /api/auth"], "kein zweiter Fehlversuch direkt hinterher"


@pytest.mark.asyncio
async def test_without_stored_password_asks_for_one():
    client, _, _ = _pihole(FakePihole(password="x"), Password(None))
    with pytest.raises(_client_mod().PiholeAuthError, match="verlangt ein Passwort"):
        await client.summary()


@pytest.mark.asyncio
async def test_without_password_on_the_pihole_works_without_session():
    client, http, _ = _pihole(FakePihole(password=None), Password(None))
    assert (await client.summary())["queries_blocked"] == 6379
    assert "X-FTL-SID" not in http.requests[-1].headers


@pytest.mark.asyncio
async def test_v5_is_detected_and_named():
    def v5(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/api.php":
            return httpx.Response(200, json={"version": 3})
        return httpx.Response(404, text="<html>404</html>")

    client, _, _ = _pihole(v5)
    with pytest.raises(_client_mod().PiholeUnsupported) as exc:
        await client.summary()
    assert str(exc.value) == "Pi-hole v5 wird nicht unterstützt – bitte auf v6 aktualisieren."
    assert exc.value.state == "unsupported"


@pytest.mark.asyncio
async def test_wrong_address_says_so():
    client, _, _ = _pihole(lambda request: httpx.Response(404, text="nope"))
    with pytest.raises(_client_mod().PiholeError, match="keine Pi-hole-Schnittstelle"):
        await client.summary()


EVIL_LOCATION = "https://angreifer.example/login?x=geheim"


def _assert_redirect_text(text: str, code: int) -> None:
    assert text.startswith(f"Pi-hole leitet auf eine andere Adresse um (HTTP {code}).")
    assert "endgültige Adresse" in text
    assert "angreifer" not in text.lower()
    assert "geheim" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
async def test_login_redirect_never_names_the_foreign_address_and_is_not_followed(code):
    client, http, _ = _pihole(lambda request: httpx.Response(code, headers={"Location": EVIL_LOCATION}))
    with pytest.raises(_client_mod().PiholeError) as exc:
        await client.summary()
    _assert_redirect_text(str(exc.value), code)
    assert http.paths() == ["POST /api/auth"]


@pytest.mark.asyncio
async def test_redirect_without_location_gives_the_same_sentence():
    client, _, _ = _pihole(lambda request: httpx.Response(302))
    with pytest.raises(_client_mod().PiholeError) as exc:
        await client.summary()
    _assert_redirect_text(str(exc.value), 302)


@pytest.mark.asyncio
@pytest.mark.parametrize("location", [
    "javascript:alert(1)",
    "http://pihole.test:angreifer-text/",
    "http://angreifer.example:99999999/x",
    "http://xn--angreifer-ey9f.example/login",
    "http://xn--angreifer-.example/",
])
async def test_unusable_location_is_no_crash_and_never_in_the_text(location):
    client, http, _ = _pihole(lambda request: httpx.Response(302, headers={"Location": location}))
    with pytest.raises(_client_mod().PiholeError) as exc:
        await client.summary()
    assert str(exc.value).startswith("Pi-hole leitet auf eine andere Adresse um")
    chain = whole_chain(exc.value)
    assert "angreifer" not in chain.lower() and "alert" not in chain
    assert exc.value.state == "unreachable"
    assert http.paths() == ["POST /api/auth"]


@pytest.mark.asyncio
async def test_invalid_own_address_is_named_as_such():
    http = FakeHttp(FakePihole())
    client = _client_mod().PiholeClient(http, base_url="http://pihole.test:abc", password=Password("geheim"))
    with pytest.raises(_client_mod().PiholeError, match="eingetragene Adresse ist ungültig"):
        await client.summary()


@pytest.mark.asyncio
@pytest.mark.parametrize("call", ["summary", "blocking", "pause", "top_clients", "gravity", "domain"])
async def test_redirect_after_login_is_an_error_even_with_a_json_body(call):
    fake = FakePihole()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/api/auth":
            return httpx.Response(302, headers={"Location": EVIL_LOCATION}, json={**_fixture("stats_summary.json"), "blocking": "enabled"})
        return fake(request)

    client, http, _ = _pihole(handler)
    calls = {
        "summary": client.summary, "blocking": client.blocking, "top_clients": client.top_clients,
        "pause": lambda: client.set_blocking(False, timer_s=300), "gravity": client.update_gravity,
        "domain": lambda: client.add_domain("deny", "werbung.example.com", comment="x"),
    }
    with pytest.raises(_client_mod().PiholeError) as exc:
        await calls[call]()
    _assert_redirect_text(str(exc.value), 302)
    assert not any(r.url.host == "angreifer.example" for r in http.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/stats/summary", "/api/stats/top_clients"])
async def test_garbage_and_deeply_nested_json_are_errors_not_crashes(path):
    fake = FakePihole()
    for content in (b"<html>kaputt</html>", b"[" * 200_000 + b"]" * 200_000):
        def handler(request: httpx.Request, content=content) -> httpx.Response:
            if request.url.path == path:
                return httpx.Response(200, content=content, headers={"Content-Type": "application/json"})
            return fake(request)

        client, _, _ = _pihole(handler)
        with pytest.raises(_client_mod().PiholeError, match="keine gültige Antwort"):
            await (client.summary() if path.endswith("summary") else client.top_clients())


@pytest.mark.asyncio
async def test_rate_limit_is_explained():
    client, _, _ = _pihole(lambda request: httpx.Response(429, json={"error": {"key": "api_seats_exceeded", "message": "API seats exceeded"}}))
    with pytest.raises(_client_mod().PiholeError, match="später erneut"):
        await client.summary()


@pytest.mark.asyncio
@pytest.mark.parametrize(("exc", "expected"), [
    (httpx.ConnectError("[Errno 111] Connection refused"), "Pi-hole ist nicht erreichbar"),
    (httpx.ReadTimeout("timed out"), "Zeitüberschreitung"),
    (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"), "Selbstsigniertes Zertifikat erlauben"),
])
async def test_network_errors_become_clean_unreachable(exc, expected):
    def boom(request: httpx.Request) -> httpx.Response:
        raise exc

    client, _, _ = _pihole(boom)
    with pytest.raises(_client_mod().PiholeError, match=expected) as err:
        await client.summary()
    assert err.value.state == "unreachable"


@pytest.mark.asyncio
async def test_pause_resume_and_logout():
    fake = FakePihole()
    client, http, _ = _pihole(fake, insecure_tls=True)

    paused = await client.set_blocking(False, timer_s=900)
    assert fake.posted == [{"blocking": False, "timer": 900}]
    assert paused == {"status": "disabled", "enabled": False, "timer_s": 900}
    resumed = await client.set_blocking(True)
    assert fake.posted[-1] == {"blocking": True, "timer": None}
    assert resumed["enabled"] is True

    await client.logout()
    assert http.paths()[-1] == "DELETE /api/auth"
    assert http.requests[-1].headers["X-FTL-SID"] == "sid-1"
    assert fake.sessions == set()
    assert set(http.insecure) == {True}
    await client.logout()
    assert http.paths()[-1] == "DELETE /api/auth"


@pytest.mark.asyncio
async def test_gravity_update_waits_long_and_returns_readable_text():
    client, http, _ = _pihole(FakePihole())
    tail = await client.update_gravity()
    assert http.paths()[-1] == "POST /api/action/gravity"
    assert http.timeouts[-1] == _client_mod().GRAVITY_TIMEOUT_S
    assert "\x1b" not in tail and "\r" not in tail
    assert "  [✓] Status: Retrieval successful" in tail.splitlines()
    assert "Status: Pending" not in tail, "überschriebene Fortschrittszeilen fallen weg"
    assert tail.endswith("[✓] Done.")


def test_gravity_tail_keeps_only_the_end():
    tail = _client_mod().gravity_tail("".join(f"Zeile {i}\n" for i in range(1000)), limit=30)
    assert len(tail) <= 30 and tail.endswith("Zeile 999") and not tail.startswith("\n")
    assert _client_mod().gravity_tail("\x07Glocke\x00 und \x1b[31mFarbe\x1b[0m\n\n") == "Glocke und Farbe"


@pytest.mark.asyncio
async def test_add_domain_posts_exactly_this_domain_and_reports_duplicates():
    fake = FakePihole()
    client, http, _ = _pihole(fake)
    assert await client.add_domain("deny", "werbung.example.com", comment="Test") == []
    assert http.paths()[-1] == "POST /api/domains/deny/exact"
    assert fake.posted[-1] == {"list": "deny", "domain": "werbung.example.com", "comment": "Test", "enabled": True}
    errors = await client.add_domain("deny", "werbung.example.com", comment="Test")
    assert errors == ["UNIQUE constraint failed: domainlist.domain, domainlist.type"]
    with pytest.raises(ValueError):
        await client.add_domain("regex", "x.example.com", comment="Test")


def test_parsers_tolerate_missing_and_odd_fields():
    c = _client_mod()
    assert c.parse_summary({}) == {
        "queries_total": None, "queries_blocked": None, "percent_blocked": None, "domains_blocked": None,
        "gravity_updated_at": None, "clients_active": None,
    }
    assert c.parse_summary({"queries": {"total": 200, "blocked": 50}, "gravity": {"domains_being_blocked": -1}})["percent_blocked"] == 25.0
    assert c.parse_blocking(_fixture("dns_blocking_paused.json")) == {"status": "disabled", "enabled": False, "timer_s": 900}
    assert c.parse_blocking({"blocking": "weird"})["status"] == "unknown"
    assert c.parse_blocking(None)["enabled"] is None
    assert c.parse_top_clients(None) == [] and c.parse_top_clients({"clients": "x"}) == []
    odd = {"clients": [None, {"ip": None, "name": None, "count": 3}, {"ip": "192.0.2.1", "count": -1},
                       {"ip": "192.0.2.2", "name": "  ein\n Name ", "count": "7"}, {"ip": "192.0.2.3", "name": "x" * 300, "count": 1}]}
    assert c.parse_top_clients(odd) == [
        {"name": "ein Name", "ip": "192.0.2.2", "count": 7},
        {"name": "x" * 99 + "…", "ip": "192.0.2.3", "count": 1},
    ]
    assert c.parse_top_clients(_fixture("top_clients.json"), limit=2)[1]["name"] == "192.0.2.12"
    assert c.domain_errors({"processed": {"errors": [{"error": "a" * 300}, "kaputt"]}}) == ["a" * 199 + "…", "unbekannter Fehler"]
    assert c.domain_errors(_fixture("domains_added.json")) == [] and c.domain_errors(None) == []


class RealHttp:
    """Wie `ctx.http`, aber mit einem echten httpx-Client (samt h11) und ohne Proxy aus der Umgebung."""

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
    answers = {**REDIRECT_ANSWERS, **BROKEN_ANSWERS}
    async with raw_http_server(answers[answer]) as base:
        client = _client_mod().PiholeClient(RealHttp(), base_url=base, password=Password("geheim"))
        with pytest.raises(_client_mod().PiholeError) as exc:
            await client.summary()
    assert str(exc.value).startswith(f"Pi-hole {sentence}")
    assert exc.value.state == "unreachable"
    chain = whole_chain(exc.value)
    assert "angreifer" not in chain.lower() and "bytearray" not in chain


@pytest.mark.asyncio
@pytest.mark.parametrize(("raised", "sentence"), [
    (httpx.LocalProtocolError("Illegal header value b'sid-geheim\\n'"), "Die Anfrage an Pi-hole ließ sich nicht senden."),
    (httpx.RemoteProtocolError("Server disconnected without sending a response."), "Pi-hole hat die Verbindung abgebrochen"),
    (httpx.RemoteProtocolError("illegal header line: bytearray(b'Location: https://angreifer.example/')"), "Pi-hole leitet auf eine andere Adresse um."),
])
async def test_protocol_errors_get_a_fixed_sentence_without_their_text(raised, sentence):
    def boom(request: httpx.Request) -> httpx.Response:
        raise raised

    client, _, _ = _pihole(boom)
    with pytest.raises(_client_mod().PiholeError) as exc:
        await client.summary()
    assert str(exc.value).startswith(sentence)
    chain = whole_chain(exc.value)
    assert "geheim" not in chain and "angreifer" not in chain and "disconnected" not in chain


def test_transport_matches_the_npm_module_apart_from_its_docstring():
    """Beide Module bringen dieselben HTTP-Helfer mit; eine Korrektur muss in beide."""
    import ast

    def body(path: Path) -> str:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        tree.body = tree.body[1:]  # Docstring
        return ast.unparse(tree)

    ours = body(EXT_SRC / "nodvard_deck_ext_pihole" / "transport.py")
    theirs = body(REPO / "extensions" / "npm" / "src" / "nodvard_deck_ext_npm" / "transport.py")
    assert ours == theirs
