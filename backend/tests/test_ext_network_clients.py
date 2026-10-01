"""network-Extension: die beiden HTTP-Clients (Pi-hole v6, Nginx Proxy Manager) gegen
`httpx.MockTransport` -- kein echter Server, kein Netzwerk. Geprueft werden Anmeldung,
zwischengespeicherte Sitzung/Token, erneute Anmeldung nach 401, das Auslesen der
Antworten und dass jeder Fehler als verstaendliche Meldung ankommt."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "network" / "src"))

from nodvard_deck_ext_network.config import (
    NotConfigured,
    normalize_url,
    npm_config,
    pihole_config,
)
from nodvard_deck_ext_network.npm import (
    NpmAuthError,
    NpmClient,
    NpmError,
    certificate_state,
    days_text,
    parse_datetime,
)
from nodvard_deck_ext_network.pihole import (
    PiholeAuthError,
    PiholeClient,
    PiholeError,
    PiholeUnsupported,
    parse_blocking,
    parse_summary,
)

PIHOLE = "http://pihole.test"
NPM = "http://npm.test:81"

SUMMARY = {
    "queries": {"total": 12345, "blocked": 1234, "percent_blocked": 9.996, "unique_domains": 800},
    "clients": {"active": 12, "total": 20},
    "gravity": {"domains_being_blocked": 123456, "last_update": 1790000000},
    "took": 0.001,
}


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


class FakePihole:
    """Minimaler Pi-hole-v6-Nachbau: Passwort pruefen, Sitzungen vergeben/entwerten."""

    def __init__(self, password: str | None = "geheim") -> None:
        self.password = password
        self.sessions: set[str] = set()
        self.counter = 0
        self.blocking = {"blocking": "enabled", "timer": None}
        self.posted: list[dict] = []

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
            return httpx.Response(200, json={"session": {"valid": True, "totp": False, "sid": sid, "csrf": "c", "validity": 1800}})
        if path == "/api/auth" and request.method == "DELETE":
            self.sessions.discard(request.headers.get("X-FTL-SID", ""))
            return httpx.Response(204)
        if self.password is not None and request.headers.get("X-FTL-SID") not in self.sessions:
            return httpx.Response(401, json={"error": {"key": "unauthorized", "message": "Unauthorized"}})
        if path == "/api/stats/summary":
            return httpx.Response(200, json=SUMMARY)
        if path == "/api/dns/blocking" and request.method == "GET":
            return httpx.Response(200, json={**self.blocking, "took": 0.0})
        if path == "/api/dns/blocking" and request.method == "POST":
            body = _body(request)
            self.posted.append(body)
            self.blocking = {"blocking": "enabled" if body["blocking"] else "disabled", "timer": body.get("timer")}
            return httpx.Response(200, json={**self.blocking, "took": 0.0})
        return httpx.Response(404, text="Not found")


def _pihole(fake, password: Password | None = None, **kwargs) -> tuple[PiholeClient, FakeHttp, Password]:
    http = FakeHttp(fake)
    password = password or Password("geheim")
    return PiholeClient(http, base_url=PIHOLE, password=password, **kwargs), http, password


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------


def test_normalize_url_adds_scheme_and_strips_admin_and_api():
    assert normalize_url("192.168.1.2", strip_suffixes=("/admin", "/api")) == "http://192.168.1.2"
    assert normalize_url("https://pi.hole/admin/", strip_suffixes=("/admin", "/api")) == "https://pi.hole"
    assert normalize_url("http://pi.hole/api", strip_suffixes=("/admin", "/api")) == "http://pi.hole"
    assert normalize_url("  ", strip_suffixes=()) is None
    with pytest.raises(ValueError):
        normalize_url("ftp://pi.hole")


def test_config_reports_missing_parts_as_friendly_not_configured():
    with pytest.raises(NotConfigured, match="noch nicht eingerichtet"):
        pihole_config({})
    with pytest.raises(NotConfigured, match="ungültig"):
        pihole_config({"pihole": {"url": "ftp://x"}})
    assert pihole_config({"pihole": {"url": "pi.hole", "tls_insecure_skip_verify": True}}).insecure_tls is True
    with pytest.raises(NotConfigured, match="E-Mail-Adresse"):
        npm_config({"npm": {"url": "http://npm:81"}})
    cfg = npm_config({"npm": {"url": "http://npm:81/api/", "identity": " admin@example.com "}})
    assert (cfg.url, cfg.identity, cfg.notify_expiring) == ("http://npm:81", "admin@example.com", True)


# ---------------------------------------------------------------------------
# Pi-hole
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pihole_logs_in_once_and_reuses_the_session():
    fake = FakePihole()
    client, http, password = _pihole(fake)

    summary = await client.summary()
    blocking = await client.blocking()
    await client.summary()

    assert summary == {
        "queries_total": 12345, "queries_blocked": 1234, "percent_blocked": 10.0, "domains_blocked": 123456,
        "gravity_updated_at": 1790000000, "clients_active": 12,
    }
    assert blocking == {"status": "enabled", "enabled": True, "timer_s": None}
    assert http.paths() == ["POST /api/auth", "GET /api/stats/summary", "GET /api/dns/blocking", "GET /api/stats/summary"]
    assert password.calls == 1, "das Passwort wird nur zur Anmeldung aus dem Vault geholt"
    assert _body(http.requests[0]) == {"password": "geheim"}
    assert all(r.headers.get("X-FTL-SID") == "sid-1" for r in http.requests[1:])
    assert "geheim" not in str(http.requests[1].url)


@pytest.mark.asyncio
async def test_pihole_logs_in_again_after_the_session_expired():
    fake = FakePihole()
    client, http, password = _pihole(fake)
    await client.summary()

    fake.expire_all()
    assert (await client.summary())["queries_total"] == 12345

    assert http.paths()[-3:] == ["GET /api/stats/summary", "POST /api/auth", "GET /api/stats/summary"]
    assert http.requests[-1].headers["X-FTL-SID"] == "sid-2"
    assert password.calls == 2


@pytest.mark.asyncio
async def test_pihole_wrong_password_is_reported_and_not_hammered():
    fake = FakePihole(password="richtig")
    client, http, _ = _pihole(fake, Password("falsch"))

    with pytest.raises(PiholeAuthError, match="Passwort abgelehnt") as first:
        await client.summary()
    assert first.value.state == "auth_failed"
    with pytest.raises(PiholeAuthError):
        await client.blocking()
    assert http.paths() == ["POST /api/auth"], "kein zweiter Fehlversuch direkt hinterher"


@pytest.mark.asyncio
async def test_pihole_without_stored_password_asks_for_one():
    client, _, _ = _pihole(FakePihole(password="x"), Password(None))
    with pytest.raises(PiholeAuthError, match="verlangt ein Passwort"):
        await client.summary()


@pytest.mark.asyncio
async def test_pihole_without_password_on_the_pihole_works_without_session():
    fake = FakePihole(password=None)
    client, http, _ = _pihole(fake, Password(None))
    assert (await client.summary())["queries_blocked"] == 1234
    assert "X-FTL-SID" not in http.requests[-1].headers


@pytest.mark.asyncio
async def test_pihole_v5_is_detected_and_named():
    def v5(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/api.php":
            return httpx.Response(200, json={"version": 3})
        return httpx.Response(404, text="<html>404</html>")

    client, _, _ = _pihole(v5)
    with pytest.raises(PiholeUnsupported) as exc:
        await client.summary()
    assert str(exc.value) == "Pi-hole v5 wird nicht unterstützt – bitte auf v6 aktualisieren."
    assert exc.value.state == "unsupported"


@pytest.mark.asyncio
async def test_pihole_wrong_address_says_so():
    client, _, _ = _pihole(lambda request: httpx.Response(404, text="nope"))
    with pytest.raises(PiholeError, match="keine Pi-hole-Schnittstelle"):
        await client.summary()


@pytest.mark.asyncio
async def test_pihole_redirect_names_the_right_address_and_is_not_followed():
    client, http, _ = _pihole(lambda request: httpx.Response(301, headers={"Location": "https://pihole.test/api/auth"}))
    with pytest.raises(PiholeError, match="leitet auf https://pihole.test weiter"):
        await client.summary()
    assert http.paths() == ["POST /api/auth"]


@pytest.mark.asyncio
async def test_pihole_rate_limit_is_explained():
    client, _, _ = _pihole(lambda request: httpx.Response(429, json={"error": {"key": "api_seats_exceeded", "message": "API seats exceeded"}}))
    with pytest.raises(PiholeError, match="später erneut"):
        await client.summary()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectError("[Errno 111] Connection refused"), "Pi-hole ist nicht erreichbar"),
        (httpx.ReadTimeout("timed out"), "Zeitüberschreitung"),
        (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"), "Selbstsigniertes Zertifikat erlauben"),
    ],
)
async def test_pihole_network_errors_become_clean_unreachable(exc, expected):
    def boom(request: httpx.Request) -> httpx.Response:
        raise exc

    client, _, _ = _pihole(boom)
    with pytest.raises(PiholeError, match=expected) as err:
        await client.summary()
    assert err.value.state == "unreachable"


@pytest.mark.asyncio
async def test_pihole_garbage_answer_is_an_error_not_a_crash():
    fake = FakePihole()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/stats/summary":
            return httpx.Response(200, text="<html>kaputt</html>")
        return fake(request)

    client, _, _ = _pihole(handler)
    with pytest.raises(PiholeError, match="keine gültige Antwort"):
        await client.summary()


@pytest.mark.asyncio
async def test_pihole_pause_resume_and_logout():
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
    await client.logout()  # ohne Sitzung: nichts zu tun, kein Fehler
    assert http.paths()[-1] == "DELETE /api/auth"


def test_pihole_parsers_tolerate_missing_fields():
    assert parse_summary({}) == {
        "queries_total": None, "queries_blocked": None, "percent_blocked": None, "domains_blocked": None,
        "gravity_updated_at": None, "clients_active": None,
    }
    assert parse_summary({"queries": {"total": 200, "blocked": 50}, "gravity": {"domains_being_blocked": -1}})["percent_blocked"] == 25.0
    assert parse_blocking({"blocking": "disabled", "timer": 299.6}) == {"status": "disabled", "enabled": False, "timer_s": 300}
    assert parse_blocking({"blocking": "weird"})["status"] == "unknown"
    assert parse_blocking(None)["enabled"] is None


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
async def test_npm_redirect_names_the_right_address():
    client, http, _ = _npm(lambda request: httpx.Response(302, headers={"Location": "https://npm.test/login"}))
    with pytest.raises(NpmError, match="leitet auf https://npm.test weiter"):
        await client.certificates()
    assert http.paths() == ["POST /api/tokens"]


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
