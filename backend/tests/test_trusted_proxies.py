"""Vertrauenswuerdiger Proxy: Einstellung `system.trusted_proxies` (core/trusted_proxies.py), Middleware
(api/proxy_headers.py), Wirkung auf Login-Drossel, Cookie und Protokoll, Anzeige `GET /system/connection`."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from nodvard_deck.api.proxy_headers import (
    SCOPE_KEY,
    Forwarding,
    ProxyHeadersMiddleware,
    apply_forwarding,
)
from nodvard_deck.config import Settings
from nodvard_deck.core import login_limit, trusted_proxies
from nodvard_deck.models import AuditEntry
from sqlalchemy import select

PASSWORD = "correct-horse-battery"
PROXY = "172.18.0.5"
"""Adresse des Proxys im Container-Netz (Beispielnetz)."""
REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _trust(*entries: str) -> trusted_proxies.TrustedProxies:
    return trusted_proxies.apply(list(entries))


def _scope(client: tuple[str, int] | None = (PROXY, 51000), headers: list[tuple[str, str]] = (), kind: str = "http") -> dict:
    return {
        "type": kind,
        "scheme": "ws" if kind == "websocket" else "http",
        "client": client,
        "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers],
    }


def _via(headers: list[tuple[str, str]], *, peer: str = PROXY, kind: str = "http") -> dict:
    scope = _scope((peer, 51000), headers, kind)
    apply_forwarding(scope, trusted_proxies.current())
    return scope


def _proxy_client(peer: str = PROXY) -> AsyncClient:
    """Ein Client, dessen Verbindung von `peer` kommt (wie ein Proxy im Container-Netz)."""
    from nodvard_deck.main import app

    transport = ASGITransport(app=app, client=(peer, 4711), raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test")


async def _bootstrap(client, username: str = "nico") -> None:
    r = await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": PASSWORD, "setup_code": "TEST-CODE-2345"})
    assert r.status_code == 201, r.text


async def _owner(client) -> dict:
    await _bootstrap(client, "owner1")
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _login(client, username: str = "nico", password: str = PASSWORD, forwarded_for: str | None = None, proto: str | None = None):
    headers = {}
    if forwarded_for is not None:
        headers["X-Forwarded-For"] = forwarded_for
    if proto is not None:
        headers["X-Forwarded-Proto"] = proto
    return await client.post("/api/v1/auth/login", json={"username": username, "password": password}, headers=headers)


def _is_secure(set_cookie_header: str) -> bool:
    return any(part.strip().lower() == "secure" for part in set_cookie_header.split(";"))


async def _audit(db_session, action: str) -> list[AuditEntry]:
    db_session.expire_all()
    return list((await db_session.execute(select(AuditEntry).where(AuditEntry.action == action))).scalars().all())


# ---------------------------------------------------------------------------
# Einstellung: Pruefung der Eintraege
# ---------------------------------------------------------------------------


def test_valid_entries_are_written_the_same_way():
    assert trusted_proxies.normalize([
        "172.18.0.5", " 192.168.2.0/24 ", "fd00:1:2::/48", "2001:DB8::10", "::ffff:172.18.0.6", "172.18.0.5/32",
        "172.18.0.5", "10.0.0.0/8", "fd00::/48", "::ffff:172.19.0.0/112",
    ]) == ["172.18.0.5", "192.168.2.0/24", "fd00:1:2::/48", "2001:db8::10", "172.18.0.6", "10.0.0.0/8", "fd00::/48", "172.19.0.0/16"]
    assert trusted_proxies.normalize([]) == []


def test_wide_ipv4_networks_only_in_private_ranges():
    assert trusted_proxies.normalize(
        ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.18.0.0/16"]
    ) == ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.18.0.0/16"]


def test_public_networks_are_limited_to_a_24_for_ipv4_and_a_48_for_ipv6():
    assert trusted_proxies.normalize(["203.0.113.0/24", "203.0.113.7/32", "203.0.113.9", "2001:db8:1::/48", "2001:db8:1:2::/64", "2001:db8:1:2::7"]) == [
        "203.0.113.0/24", "203.0.113.7", "203.0.113.9", "2001:db8:1::/48", "2001:db8:1:2::/64", "2001:db8:1:2::7",
    ]
    for too_big in ("203.0.112.0/23", "203.0.0.0/16", "203.0.0.0/8", "2001:db8::/47", "2001:db8::/32", "2001::/16"):
        with pytest.raises(trusted_proxies.InvalidEntry) as exc:
            trusted_proxies.normalize([too_big])
        assert "zu groß" in str(exc.value) and "ab /24" in str(exc.value) and "ab /48" in str(exc.value), too_big


def test_ipv6_inside_and_outside_the_home_share_one_limit_a_48():
    """Ein ULA-Netz eines Standorts ist ein /48, Docker legt Bruecken meist als /64 an: beides geht, alles Groessere nicht."""
    assert trusted_proxies.normalize(["fd00:1:2::/48", "fd00:1:2:3::/64", "fd00:1:2:3::5", "fe80::/64", "::1", "fe80::1"]) == [
        "fd00:1:2::/48", "fd00:1:2:3::/64", "fd00:1:2:3::5", "fe80::/64", "::1", "fe80::1",
    ]
    for too_big in ("fd00:1:2::/47", "fd00::/32", "fd00::/8", "fc00::/7", "fe80::/10", "fe80::/32"):
        with pytest.raises(trusted_proxies.InvalidEntry, match="zu groß"):
            trusted_proxies.normalize([too_big])


def test_zone_of_an_address_is_dropped():
    assert trusted_proxies.parse_address("fe80::1%eth0") == trusted_proxies.parse_address("fe80::1")
    assert str(trusted_proxies.parse_address("fe80::1%x y")) == "fe80::1"
    scope = _scope(headers=[("X-Forwarded-For", "fe80::7%eth0")])
    _trust(PROXY)
    apply_forwarding(scope, trusted_proxies.current())
    assert scope["client"][0] == "fe80::7"


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("proxy.local", "keine IP-Adresse"),
        ("", "keine IP-Adresse"),
        ("172.18.0.300", "keine IP-Adresse"),
        ("fe80::1%eth0", "keine IP-Adresse"),
        ("1" * 80, "keine IP-Adresse"),
        ("0.0.0.0/0", "zu groß"),
        ("::/0", "zu groß"),
        ("10.0.0.0/7", "zu groß"),
        ("2001:db8::/31", "zu groß"),
        ("2001:db8::/47", "zu groß"),
        ("::ffff:0:0/96", "zu groß"),  # alle IPv4-Adressen in IPv6-Schreibweise
        ("172.0.0.0/8", "zu groß"),  # Vertipper fuer 172.16.0.0/12, umfasst fremde Netze
        ("0.0.0.0/8", "zu groß"),
        ("8.0.0.0/8", "zu groß"),
        ("203.0.0.0/15", "zu groß"),
        ("203.0.112.0/23", "zu groß"),
        ("192.168.0.0/15", "zu groß"),  # groesser als der private Bereich 192.168.0.0/16
        ("192.168.2.40/24", "„192.168.2.40“ ein, für das ganze Netz „192.168.2.0/24“"),
        ("0.0.0.0", "keine Adresse, von der ein Proxy kommen kann"),
        ("::", "keine Adresse, von der ein Proxy kommen kann"),
        ("224.0.0.1", "keine Adresse, von der ein Proxy kommen kann"),
        (5, "als Text"),
    ],
)
def test_invalid_entries_are_refused(entry, expected):
    with pytest.raises(trusted_proxies.InvalidEntry) as exc:
        trusted_proxies.normalize([entry])
    assert expected in str(exc.value)


def test_at_most_twenty_entries():
    many = [f"192.168.2.{i}" for i in range(1, 22)]
    assert len(trusted_proxies.normalize(many[:20])) == 20
    with pytest.raises(trusted_proxies.InvalidEntry, match="Höchstens 20"):
        trusted_proxies.normalize(many)


def test_environment_variable_is_read_leniently(monkeypatch, caplog):
    monkeypatch.delenv("NODVARD_DECK_TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("LATTICE_TRUSTED_PROXIES", raising=False)
    assert Settings().trusted_proxies is None
    monkeypatch.setenv("NODVARD_DECK_TRUSTED_PROXIES", "")
    assert Settings().trusted_proxies is None, "leer zählt als nicht gesetzt"
    assert trusted_proxies.env_entries(Settings()) is None
    monkeypatch.setenv("NODVARD_DECK_TRUSTED_PROXIES", " 172.18.0.5, proxy.local, 0.0.0.0/0,,192.168.2.0/24 ")
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.trusted_proxies"):
        assert trusted_proxies.env_entries(Settings()) == ["172.18.0.5", "192.168.2.0/24"]
    reasons = [r.getMessage().split("reason=", 1)[1] for r in caplog.records if "trusted_proxies_entry_ignored" in r.getMessage()]
    assert [reason.split("“", 1)[0] for reason in reasons] == ["„proxy.local", "„0.0.0.0/0"]
    monkeypatch.delenv("NODVARD_DECK_TRUSTED_PROXIES")
    monkeypatch.setenv("LATTICE_TRUSTED_PROXIES", "172.18.0.7")
    assert trusted_proxies.env_entries(Settings()) == ["172.18.0.7"], "der alte Name gilt weiter"


@pytest.mark.asyncio
async def test_start_loads_the_saved_list_or_the_variable(db_session, test_settings):
    from nodvard_deck.services import settings as settings_service

    test_settings.trusted_proxies = None
    assert (await trusted_proxies.load(db_session, test_settings)).entries == ()
    # Ein von Hand verbogener Wert in der Datenbank: Unbrauchbares faellt weg, nie ein Startfehler.
    await settings_service.set_global(db_session, trusted_proxies.SETTING_KEY, ["172.18.0.5", "0.0.0.0/0", 7])
    loaded = await trusted_proxies.load(db_session, test_settings)
    assert loaded.entries == ("172.18.0.5",) and loaded.source == "setting"
    assert trusted_proxies.current() is loaded
    test_settings.trusted_proxies = "192.168.2.1"
    loaded = await trusted_proxies.load(db_session, test_settings)
    assert loaded.entries == ("192.168.2.1",) and loaded.source == "env"


@pytest.mark.asyncio
async def test_start_without_readable_list_trusts_nobody(monkeypatch, test_settings):
    from nodvard_deck import main

    _trust(PROXY)

    async def _broken(session, settings):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(trusted_proxies, "load", _broken)
    await main._load_trusted_proxies(test_settings)
    assert trusted_proxies.current().entries == ()


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


def test_trusted_peer_gives_the_client_from_forwarded_for():
    _trust(PROXY)
    scope = _via([("X-Forwarded-For", "192.168.2.40"), ("X-Forwarded-Proto", "https")])
    assert scope["client"] == ("192.168.2.40", 0)
    assert scope["scheme"] == "https"
    assert scope[SCOPE_KEY] == Forwarding(peer=PROXY, trusted=True, forwarded_headers=True)


def test_rightmost_untrusted_address_counts_never_what_the_client_wrote():
    _trust(PROXY, "172.18.0.6")
    # Der Client schickt selbst eine erfundene Adresse mit, der erste Proxy haengt die echte an, der zweite seine.
    assert _via([("X-Forwarded-For", "203.0.113.9, 192.168.2.40, 172.18.0.6")])["client"] == ("192.168.2.40", 0)
    # Zwei Kopfzeilen zaehlen wie eine durch Kommas getrennte; Leerzeichen und leere Stuecke stoeren nicht.
    two = _via([("X-Forwarded-For", "203.0.113.9"), ("X-Forwarded-For", " 192.168.2.41 ,, ")])
    assert two["client"] == ("192.168.2.41", 0)


def test_broken_entries_are_never_skipped_to_reach_further_left():
    _trust(PROXY, "172.18.0.6")
    # Rechts unlesbar: die Gegenstelle bleibt, die Adresse links davon (vom Client geschrieben) zaehlt nicht.
    assert _via([("X-Forwarded-For", "192.168.2.40, unknown")])["client"] == (PROXY, 51000)
    # Hinter einem gepruefte Proxy unlesbar: es bleibt die zuletzt gepruefte Adresse.
    assert _via([("X-Forwarded-For", "203.0.113.9, kaputt, 172.18.0.6")])["client"] == ("172.18.0.6", 0)
    for junk in ("", " , ", "192.168.2.40:99999", "192.168.2.40:\xb2", "[2001:db8::1]:\xb9", "[2001:db8::1", "[2001:db8::1]x",
                 "1.2.3.4:5:6", "\x00"):
        assert _via([("X-Forwarded-For", junk)])["client"] == (PROXY, 51000), junk


def test_an_unexpected_error_leaves_the_request_as_without_proxy(monkeypatch, caplog):
    from nodvard_deck.api import proxy_headers

    _trust(PROXY)

    def _boom(entries, trust):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(proxy_headers, "client_from_chain", _boom)
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.trusted_proxies"):
        scope = _via([("X-Forwarded-For", "192.168.2.40"), ("X-Forwarded-Proto", "https")])
    assert scope["client"] == (PROXY, 51000) and scope["scheme"] == "http"
    assert "forwarded_headers_unreadable" in caplog.text and "Traceback" not in caplog.text


def test_ports_brackets_and_ipv4_in_ipv6_notation():
    _trust(PROXY)
    assert _via([("X-Forwarded-For", "192.168.2.40:5555")])["client"] == ("192.168.2.40", 5555)
    assert _via([("X-Forwarded-For", "[2001:db8::7]:443")])["client"] == ("2001:db8::7", 443)
    assert _via([("X-Forwarded-For", "2001:DB8::7")])["client"] == ("2001:db8::7", 0)
    assert _via([("X-Forwarded-For", "::ffff:192.168.2.40")])["client"] == ("192.168.2.40", 0)
    # Lauscht der Server auf `::`, meldet er eine IPv4-Gegenstelle so -- sie ist trotzdem der eingetragene Proxy.
    scope = _via([("X-Forwarded-For", "192.168.2.40")], peer=f"::ffff:{PROXY}")
    assert scope["client"] == ("192.168.2.40", 0) and scope[SCOPE_KEY].trusted


def test_chain_of_only_trusted_proxies_ends_at_the_leftmost():
    _trust(PROXY, "172.18.0.0/16")
    assert _via([("X-Forwarded-For", "172.18.0.9, 172.18.0.6")])["client"] == ("172.18.0.9", 0)


def test_a_forged_address_is_only_ignored_while_just_the_proxy_is_entered():
    forged = "203.0.113.9"
    chain = [("X-Forwarded-For", f"{forged}, 172.18.0.77")]  # das Geraet 172.18.0.77 schickt eine erfundene Adresse mit
    _trust(PROXY)
    assert _via(chain)["client"] == ("172.18.0.77", 0), "nur der Proxy eingetragen: die erfundene Adresse wird nie gelesen"
    assert _via([("X-Forwarded-For", forged)], peer="172.18.0.77")["client"] == ("172.18.0.77", 51000)
    # Ein ganzes Netz eingetragen: jedes Geraet darin gilt als Proxy, seine Angaben werden geglaubt.
    _trust("172.18.0.0/24")
    assert _via(chain)["client"] == (forged, 0)
    assert _via([("X-Forwarded-For", forged)], peer="172.18.0.77")["client"] == (forged, 0)


def test_only_the_last_32_entries_of_forwarded_for_are_looked_at():
    from nodvard_deck.api.proxy_headers import MAX_HOPS

    assert MAX_HOPS == 32
    _trust(PROXY, "172.18.0.6")
    forged = "203.0.113.9"
    # Genau 32 Eintraege: der linke ist noch dabei und zaehlt als Client.
    assert _via([("X-Forwarded-For", ", ".join([forged] + ["172.18.0.6"] * (MAX_HOPS - 1)))])["client"] == (forged, 0)
    # Ein Eintrag mehr: der linke wird nie gelesen. Es bleibt der aelteste angesehene, und der ist ein eingetragener Proxy.
    for extra in (1, 2, 40, 5000):
        chain = ", ".join([forged] + ["172.18.0.6"] * (MAX_HOPS - 1 + extra))
        assert _via([("X-Forwarded-For", chain)])["client"] == ("172.18.0.6", 0), extra
    # Verteilt auf mehrere Kopfzeilen zaehlt es genauso.
    two = [("X-Forwarded-For", ", ".join([forged] + ["172.18.0.6"] * 20)), ("X-Forwarded-For", ", ".join(["172.18.0.6"] * 12))]
    assert _via(two)["client"] == ("172.18.0.6", 0)
    # Steht ein fremder Eintrag innerhalb der letzten 32, gilt er wie sonst.
    inside = ", ".join([forged, "192.168.2.40"] + ["172.18.0.6"] * (MAX_HOPS - 2))
    assert _via([("X-Forwarded-For", inside)])["client"] == ("192.168.2.40", 0)


def test_a_huge_forwarded_for_header_is_harmless_and_fast():
    import time

    _trust(PROXY, "172.18.0.6")
    size = 64 * 1024
    cases = {
        "nur Kommas": ("," * size, (PROXY, 51000)),
        "Kommas und Leerzeichen": (", " * (size // 2), (PROXY, 51000)),
        "Muell ohne Komma": ("x" * size, (PROXY, 51000)),
        "Muell mit Kommas": ("x," * (size // 2), (PROXY, 51000)),
        "lauter Zeichen, die nur fast Adressen sind": ("1.2.3.4:99999," * (size // 14), (PROXY, 51000)),
        "ein einziger Eintrag ohne Ende": ("1" * size, (PROXY, 51000)),
        "viele gueltige Adressen": ("192.168.2.40," * (size // 13), ("192.168.2.40", 0)),
        "Adresse hinter Muell": ("x," * (size // 4) + "192.168.2.40", ("192.168.2.40", 0)),
        "Muell hinter Adresse": ("192.168.2.40," + "x," * (size // 4), (PROXY, 51000)),
        "viele Proxys und links eine Adresse": ("203.0.113.9," + "172.18.0.6," * (size // 11), ("172.18.0.6", 0)),
    }
    for name, (header, expected) in cases.items():
        start = time.perf_counter()
        scope = _via([("X-Forwarded-For", header), ("X-Forwarded-Proto", "https")])
        assert time.perf_counter() - start < 2, name
        assert scope["client"] == expected, name
        assert scope["scheme"] == "https", name
        assert scope[SCOPE_KEY].trusted and scope[SCOPE_KEY].forwarded_headers, name


@pytest.mark.asyncio
async def test_a_huge_forwarded_for_header_does_not_break_a_request(client):
    headers = await _owner(client)
    _trust(PROXY)
    async with _proxy_client() as proxy:
        for junk in ("x," * (32 * 1024), "," * (64 * 1024), "192.168.2.40," * 5000):
            r = await proxy.get("/api/v1/system/connection", headers={**headers, "X-Forwarded-For": junk})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["via_trusted_proxy"] is True and body["peer"] == PROXY
            assert body["client"] == ("192.168.2.40" if junk.startswith("192") else PROXY)


def test_untrusted_peer_headers_are_ignored():
    _trust(PROXY)
    scope = _via([("X-Forwarded-For", "192.168.2.40"), ("X-Forwarded-Proto", "https")], peer="192.168.2.66")
    assert scope["client"] == ("192.168.2.66", 51000) and scope["scheme"] == "http"
    assert scope[SCOPE_KEY] == Forwarding(peer="192.168.2.66", trusted=False, forwarded_headers=True)


def test_empty_list_changes_nothing_even_for_localhost():
    trusted_proxies.reset()
    for peer in (PROXY, "127.0.0.1", "::1"):
        scope = _via([("X-Forwarded-For", "192.168.2.40"), ("X-Forwarded-Proto", "https")], peer=peer)
        assert scope["client"] == (peer, 51000) and scope["scheme"] == "http"


def test_connection_without_ip_is_never_a_proxy():
    _trust(PROXY)
    scope = _scope(None, [("X-Forwarded-For", "192.168.2.40")])
    apply_forwarding(scope, trusted_proxies.current())
    assert scope["client"] is None
    assert scope[SCOPE_KEY] == Forwarding(peer=None, trusted=False, forwarded_headers=True)
    scope = _scope(("unix-socket", 0), [("X-Forwarded-For", "192.168.2.40")])
    apply_forwarding(scope, trusted_proxies.current())
    assert scope["client"] == ("unix-socket", 0)


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        ("http", "https", "https"),
        ("http", " HTTPS ", "https"),
        ("http", "http", "http"),
        ("http", "wss", "https"),
        ("http", "https, http", "http"),  # der rechteste Wert: der des direkten Proxys
        ("http", "ftp", "http"),
        ("http", "", "http"),
        ("websocket", "https", "wss"),
        ("websocket", "wss", "wss"),
        ("websocket", "http", "ws"),
        ("websocket", "gopher", "ws"),
    ],
)
def test_forwarded_proto(kind, value, expected):
    _trust(PROXY)
    assert _via([("X-Forwarded-Proto", value)], kind=kind)["scheme"] == expected


@pytest.mark.asyncio
async def test_websocket_scope_gets_the_real_client():
    _trust(PROXY)
    seen = {}

    async def _app(scope, receive, send):
        seen.update(client=scope.get("client"), scheme=scope.get("scheme"))

    middleware = ProxyHeadersMiddleware(_app)
    await middleware(_scope((PROXY, 1), [("X-Forwarded-For", "192.168.2.40"), ("X-Forwarded-Proto", "https")], "websocket"), None, None)
    assert seen == {"client": ("192.168.2.40", 0), "scheme": "wss"}
    # Lifespan-Scopes laufen unberuehrt durch.
    lifespan = {"type": "lifespan"}
    await middleware(lifespan, None, None)
    assert SCOPE_KEY not in lifespan


def test_middleware_sits_outside_everything_and_uvicorn_trusts_nobody_by_itself():
    from nodvard_deck.main import app

    assert app.user_middleware[0].cls is ProxyHeadersMiddleware, "ganz aussen: alles danach sieht die echte Adresse"
    text = (REPO / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    args = re.findall(r'"([^"]*)"', re.search(r"^CMD \[(.*)\]\s*$", code, re.MULTILINE).group(1))
    assert "--no-proxy-headers" in args, "sonst glaubt uvicorn vorab jeder Anfrage von 127.0.0.1"


def test_guides_tell_where_forwarded_allow_ips_went():
    """Mit `--no-proxy-headers` wirkt `FORWARDED_ALLOW_IPS` nicht mehr: beide Anleitungen sagen, wo die Adresse stattdessen
    hingehoert."""
    for name, place in (("12-REVERSE-PROXY.md", "Einstellungen → System → Vertrauenswürdiger Proxy"), ("en/REVERSE-PROXY.md", "Settings → System → Trusted proxy")):
        text = " ".join((REPO / "docs" / name).read_text(encoding="utf-8").split())
        after = text[text.index("FORWARDED_ALLOW_IPS"):]
        assert place in after[:300], name


# ---------------------------------------------------------------------------
# Wirkung: Login-Drossel, Cookie, Protokoll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_limit_counts_each_real_client_behind_the_proxy(client, db_session):
    await _bootstrap(client)
    _trust(PROXY)
    async with _proxy_client() as proxy:
        for _ in range(login_limit.MAX_FAILURES_PER_USER):
            assert (await _login(proxy, password="falsch", forwarded_for="192.168.2.40")).status_code == 401
        assert (await _login(proxy, forwarded_for="192.168.2.40")).status_code == 429
        # Ein anderes Geraet hinter demselben Proxy ist nicht mitgesperrt.
        assert (await _login(proxy, forwarded_for="192.168.2.41")).status_code == 200
    failed = await _audit(db_session, "login.failed")
    assert {entry.ip for entry in failed} == {"192.168.2.40"}, "das Protokoll nennt die echte Adresse"


@pytest.mark.asyncio
async def test_forged_forwarded_for_from_an_untrusted_peer_changes_nothing(client, db_session):
    await _bootstrap(client)
    _trust(PROXY)
    async with _proxy_client("192.168.2.66") as attacker:
        for i in range(login_limit.MAX_FAILURES_PER_USER):
            r = await _login(attacker, password="falsch", forwarded_for=f"10.0.0.{i}")
            assert r.status_code == 401
        assert (await _login(attacker, forwarded_for="10.9.9.9")).status_code == 429
    failed = await _audit(db_session, "login.failed")
    assert {entry.ip for entry in failed} == {"192.168.2.66"}


@pytest.mark.asyncio
async def test_secure_cookie_behind_an_https_proxy(client, test_settings):
    await _bootstrap(client)
    test_settings.cookie_secure = None
    _trust(PROXY)
    async with _proxy_client() as proxy:
        r = await _login(proxy, forwarded_for="192.168.2.40", proto="https")
        assert r.status_code == 200 and _is_secure(r.headers["set-cookie"])
        r = await _login(proxy, forwarded_for="192.168.2.40", proto="http")
        assert r.status_code == 200 and not _is_secure(r.headers["set-cookie"])
    async with _proxy_client("192.168.2.66") as untrusted:
        r = await _login(untrusted, forwarded_for="192.168.2.40", proto="https")
        assert r.status_code == 200 and not _is_secure(r.headers["set-cookie"]), "Kopfzeilen eines Fremden zählen nicht"


# ---------------------------------------------------------------------------
# Einstellungen-API
# ---------------------------------------------------------------------------


def _value(listing: list[dict]) -> object:
    return next(row["value"] for row in listing if row["key"] == "system.trusted_proxies")


@pytest.mark.asyncio
async def test_settings_api_saves_checks_and_logs(client, db_session, test_settings):
    test_settings.trusted_proxies = None
    headers = await _owner(client)
    assert _value((await client.get("/api/v1/settings", headers=headers)).json()) == []

    r = await client.put("/api/v1/settings/system.trusted_proxies", json={"value": [" 172.18.0.5", "fd00:1::/64", "::ffff:172.18.0.6"]}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"key": "system.trusted_proxies", "value": ["172.18.0.5", "fd00:1::/64", "172.18.0.6"]}
    assert _value((await client.get("/api/v1/settings", headers=headers)).json()) == ["172.18.0.5", "fd00:1::/64", "172.18.0.6"]
    assert trusted_proxies.current().entries == ("172.18.0.5", "fd00:1::/64", "172.18.0.6")

    for bad, text in (
        ("172.18.0.5", "erwartet list"),
        (["proxy.local"], "keine IP-Adresse"),
        (["0.0.0.0/0"], "zu groß"),
        ([f"192.168.2.{i}" for i in range(1, 22)], "Höchstens 20"),
    ):
        r = await client.put("/api/v1/settings/system.trusted_proxies", json={"value": bad}, headers=headers)
        assert r.status_code == 422 and text in r.json()["detail"], r.text
    assert trusted_proxies.current().entries == ("172.18.0.5", "fd00:1::/64", "172.18.0.6"), "nichts Ungueltiges gilt"

    r = await client.put("/api/v1/settings/system.trusted_proxies", json={"value": []}, headers=headers)
    assert r.status_code == 200
    changes = [e.detail for e in await _audit(db_session, "system.settings.changed") if e.target_id == "system.trusted_proxies"]
    assert changes == [
        {"key": "system.trusted_proxies", "old": [], "new": ["172.18.0.5", "fd00:1::/64", "172.18.0.6"]},
        {"key": "system.trusted_proxies", "old": ["172.18.0.5", "fd00:1::/64", "172.18.0.6"], "new": []},
    ]


@pytest.mark.asyncio
async def test_environment_variable_wins_and_refuses_changes(client, test_settings, caplog):
    headers = await _owner(client)
    test_settings.trusted_proxies = "172.18.0.9, 192.168.2.0/24, kaputt"
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.trusted_proxies"):
        assert _value((await client.get("/api/v1/settings", headers=headers)).json()) == ["172.18.0.9", "192.168.2.0/24"]
    assert "kaputt" not in caplog.text, "gewarnt wird beim Start, nicht bei jedem Anzeigen"
    r = await client.put("/api/v1/settings/system.trusted_proxies", json={"value": ["172.18.0.5"]}, headers=headers)
    assert r.status_code == 409 and "NODVARD_DECK_TRUSTED_PROXIES" in r.json()["detail"]
    assert trusted_proxies.current().entries == ()


@pytest.mark.asyncio
async def test_a_change_takes_effect_without_restart(client, test_settings):
    test_settings.trusted_proxies = None
    headers = await _owner(client)
    forwarded = {**headers, "X-Forwarded-For": "192.168.2.40", "X-Forwarded-Proto": "https"}
    async with _proxy_client() as proxy:
        before = (await proxy.get("/api/v1/system/connection", headers=forwarded)).json()
        assert (before["client"], before["scheme"], before["via_trusted_proxy"]) == (PROXY, "http", False)
        assert before["forwarded_headers"] is True

        r = await client.put("/api/v1/settings/system.trusted_proxies", json={"value": [PROXY]}, headers=headers)
        assert r.status_code == 200
        after = (await proxy.get("/api/v1/system/connection", headers=forwarded)).json()
        assert after == {
            "peer": PROXY, "client": "192.168.2.40", "scheme": "https", "via_trusted_proxy": True,
            "forwarded_headers": True, "trusted_proxies": [PROXY], "trusted_proxies_source": "setting",
            "untrusted_proxies": [],
        }

        assert (await client.put("/api/v1/settings/system.trusted_proxies", json={"value": []}, headers=headers)).status_code == 200
        again = (await proxy.get("/api/v1/system/connection", headers=forwarded)).json()
        assert (again["client"], again["scheme"], again["via_trusted_proxy"]) == (PROXY, "http", False)


# ---------------------------------------------------------------------------
# Anzeige: GET /system/connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_route_needs_system_read(client, db_session):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    assert (await client.get("/api/v1/system/connection")).status_code == 401
    await _owner(client)
    roles = await auth_service.ensure_builtin_roles(db_session)
    viewer = User(username="gast", password_hash=security.hash_password("whatever-1234"), is_active=True)
    viewer.roles.append(roles["viewer"])
    db_session.add(viewer)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": "gast", "password": "whatever-1234"})
    token = login.json()["access_token"]
    assert (await client.get("/api/v1/system/connection", headers={"Authorization": f"Bearer {token}"})).status_code == 403


@pytest.mark.asyncio
async def test_connection_route_describes_a_direct_request(client, test_settings):
    test_settings.trusted_proxies = None
    headers = await _owner(client)
    r = await client.get("/api/v1/system/connection", headers=headers)
    assert r.status_code == 200
    assert r.json() == {
        "peer": "127.0.0.1", "client": "127.0.0.1", "scheme": "http", "via_trusted_proxy": False,
        "forwarded_headers": False, "trusted_proxies": [], "trusted_proxies_source": "setting", "untrusted_proxies": [],
    }
    test_settings.trusted_proxies = "172.18.0.9"
    assert (await client.get("/api/v1/system/connection", headers=headers)).json()["trusted_proxies_source"] == "env"


@pytest.mark.asyncio
async def test_only_the_owner_changes_the_proxy_list(client, db_session):
    """Nicos Entscheidung: Wessen Angaben zur Absender-Adresse gelten, legt nur der Inhaber fest, nicht jeder Admin."""
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    owner = await _owner(client)
    roles = await auth_service.ensure_builtin_roles(db_session)
    admin = User(username="admin2", password_hash=security.hash_password("whatever-1234"), is_active=True)
    admin.roles.append(roles["admin"])
    db_session.add(admin)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": "admin2", "password": "whatever-1234"})
    admin_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    denied = await client.put("/api/v1/settings/system.trusted_proxies", json={"value": ["172.18.0.5"]}, headers=admin_headers)
    assert denied.status_code == 403
    assert "Inhaber" in denied.json()["detail"]
    # Auch ein ungueltiger Wert ergibt 403, nicht 422 -- und gespeichert ist nichts.
    assert (await client.put("/api/v1/settings/system.trusted_proxies", json={"value": "kaputt"}, headers=admin_headers)).status_code == 403
    listed = {r["key"]: r["value"] for r in (await client.get("/api/v1/settings", headers=owner)).json()}
    assert listed["system.trusted_proxies"] == []
    entries = (await db_session.execute(
        select(AuditEntry).where(AuditEntry.target_id == "system.trusted_proxies", AuditEntry.outcome == "denied")
    )).scalars().all()
    assert len(entries) == 2
    # Andere Einstellungen darf der Admin weiter aendern.
    assert (await client.put("/api/v1/settings/system.timezone", json={"value": "Europe/Berlin"}, headers=admin_headers)).status_code == 200
    allowed = await client.put("/api/v1/settings/system.trusted_proxies", json={"value": ["172.18.0.5"]}, headers=owner)
    assert allowed.status_code == 200, allowed.text
