"""Vertrauenswuerdige Proxys (Einstellung `system.trusted_proxies`): wessen Angaben `X-Forwarded-For` und
`X-Forwarded-Proto` Nodvard Deck glaubt.

Steht ein Reverse-Proxy (Nginx Proxy Manager, Traefik, Caddy) vor Nodvard Deck, ist die Gegenstelle jeder
Verbindung der Proxy. Ohne seine Kopfzeilen saehe die Login-Drossel alle Menschen als eine einzige Adresse, das
Protokoll ebenso, und hinter HTTPS bekaeme das Anmelde-Cookie kein `Secure`. Dieselben Kopfzeilen kann aber jeder
Client selbst schicken: ausgewertet werden sie nur, wenn die direkte Gegenstelle in dieser Liste steht
(`api/proxy_headers.py`). Leere Liste (Vorgabe) = niemandem vertrauen, wie vor dieser Einstellung.

Regeln fuer die Liste (`normalize`):
- einzelne Adressen oder Netze (CIDR), IPv4 und IPv6, hoechstens `MAX_ENTRIES`;
- eine IPv4-Adresse in IPv6-Schreibweise (`::ffff:172.18.0.5`) zaehlt als IPv4-Adresse, auch beim Vergleich mit
  der Gegenstelle (ein Server, der auf `::` lauscht, meldet IPv4-Gegenstellen so);
- zu grosse Netze sind verboten, denn jeder Rechner darin duerfte seine Adresse faelschen (`0.0.0.0/0` hiesse:
  allen glauben). Oeffentliche IPv4-Netze hoechstens /24 (`MIN_PREFIX_V4_PUBLIC`): ein Proxy steht auf einem
  Rechner oder in einem kleinen Netz, schon ein /16 umfasste 65 536 fremde Adressen. Private bzw. interne
  IPv4-Bereiche (`_PRIVATE_V4`: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 127.0.0.0/8,
  169.254.0.0/16) duerfen so gross sein wie der Bereich selbst (bis /8, `MIN_PREFIX_V4`): dort sitzen nur eigene
  Geraete, und ein Docker-Netz wie 172.18.0.0/16 ist ueblich. `172.0.0.0/8` statt `172.16.0.0/12` waere dagegen ein
  Vertipper mit fremden Netzen.
  IPv6 hoechstens /48 (`MIN_PREFIX_V6`), fuer oeffentliche wie interne Adressen gleich. Eine weitere Grenze fuer
  interne Adressen (ULA `fc00::/7`, Link-Local `fe80::/10`, `::1`) braucht es nicht: ein ULA-Netz eines ganzen
  Standorts ist nach RFC 4193 genau ein /48, und Docker legt IPv6-Netze meist als /64 an, das ist enger als ein /48
  und geht also. Ein /32 waere dagegen ein ganzer Anbieter. Zu gross sind damit auch `fc00::/7` und `fe80::/10`;
  einzelne Adressen (`::1`, `fd00::5`) sind immer in Ordnung;
- eine Adresse mit Netzmaske, deren hintere Stellen nicht null sind (`192.168.2.40/24`), wird abgelehnt statt still
  zum ganzen Netz erweitert: gemeint war oft nur der eine Rechner;
- keine Zonen-Angabe (`%eth0`), keine Multicast-Adressen, nicht `0.0.0.0`/`::`: von dort kommt keine Verbindung.

Der gueltige Stand liegt im Prozess (`current()`), nie wird je Anfrage die Datenbank gefragt: beim Start geladen
(`load`), nach `PUT /settings/system.trusted_proxies` ersetzt (`apply`). Ist `NODVARD_DECK_TRUSTED_PROXIES` gesetzt
(Komma-getrennt, `config.Settings.trusted_proxies`), gilt nur sie; ungueltige Eintraege darin werden mit einer
Warnung im Protokoll uebersprungen (lieber weniger vertrauen als nicht starten).
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..services import settings as settings_service

logger = logging.getLogger("nodvard_deck.trusted_proxies")

SETTING_KEY = "system.trusted_proxies"
ENV_NAME = "NODVARD_DECK_TRUSTED_PROXIES"
MAX_ENTRIES = 20
MAX_ENTRY_CHARS = 64
"""Laenger ist keine Adresse und kein Netz (die laengste IPv6-Schreibweise mit Praefix hat 50 Zeichen)."""
MIN_PREFIX_V4 = 8
"""Kleinstes Praefix fuer IPv4-Netze in `_PRIVATE_V4` (der Bereich selbst ist die eigentliche Grenze: kein Netz darf
ueber ihn hinausgehen)."""
MIN_PREFIX_V4_PUBLIC = 24
"""Kleinstes Praefix fuer alle anderen (oeffentlichen) IPv4-Netze."""
MIN_PREFIX_V6 = 48
"""Kleinstes Praefix fuer IPv6-Netze, gleich fuer oeffentliche und interne (siehe Modulkopf)."""

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Source = Literal["setting", "env"]

_MAPPED_V4 = ipaddress.IPv6Network("::ffff:0:0/96")
_PRIVATE_V4 = tuple(
    ipaddress.IPv4Network(net)
    for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16")
)
"""Private bzw. interne IPv4-Bereiche (Heimnetz, Docker, Tailscale/CGNAT, der Rechner selbst, Link-Local), in denen
Netze bis zur Groesse des Bereichs erlaubt sind (hoechstens /8)."""
_EXAMPLE = "zum Beispiel 172.18.0.5 oder 172.18.0.0/28"


class InvalidEntry(ValueError):
    """Ein Eintrag der Liste ist keine erlaubte Adresse bzw. kein erlaubtes Netz. Der Text ist ein fertiger deutscher
    Satz fuer die Oberflaeche."""


def parse_address(text: object) -> Address | None:
    """Eine einzelne Adresse (ohne Praefix und Port), IPv4 in IPv6-Schreibweise als IPv4. `None`, wenn es keine ist
    (z. B. bei einer Gegenstelle ohne IP, etwa einem Unix-Socket). Eine Zonen-Angabe (`fe80::1%eth0`) faellt weg:
    sie sagt nur, ueber welche Netzwerkkarte die Adresse erreichbar ist, und gehoert weder in die Drossel noch ins
    Protokoll."""
    if not isinstance(text, str) or not text or len(text) > MAX_ENTRY_CHARS:
        return None
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.scope_id is not None:
        ip = ipaddress.IPv6Address(int(ip))
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _shown(raw: str) -> str:
    text = raw.strip()
    return text if len(text) <= MAX_ENTRY_CHARS else text[:MAX_ENTRY_CHARS] + "…"


def parse_entry(raw: object) -> Network:
    """Ein Eintrag der Liste als Netz (eine einzelne Adresse als /32 bzw. /128). `InvalidEntry`, wenn er nicht
    erlaubt ist (siehe Modul-Docstring)."""
    if not isinstance(raw, str):
        raise InvalidEntry(f"Jeder Eintrag muss eine Adresse oder ein Netz als Text sein, {_EXAMPLE}.")
    text = raw.strip()
    not_an_address = InvalidEntry(f"„{_shown(raw)}“ ist keine IP-Adresse und kein Netz ({_EXAMPLE}).")
    if not text or len(text) > MAX_ENTRY_CHARS or "%" in text:
        raise not_an_address
    if "/" in text:
        try:
            net: Network = ipaddress.ip_network(text, strict=True)
        except ValueError:
            try:
                loose = ipaddress.ip_network(text, strict=False)
            except ValueError:
                raise not_an_address from None
            address = text.split("/", 1)[0]
            raise InvalidEntry(
                f"„{text}“ ist eine Adresse mit Netzmaske. Für nur diesen Rechner trag „{address}“ ein, "
                f"für das ganze Netz „{loose}“."
            ) from None
    else:
        try:
            net = ipaddress.ip_network(ipaddress.ip_address(text))
        except ValueError:
            raise not_an_address from None
    if isinstance(net, ipaddress.IPv6Network) and net.prefixlen >= 96 and net.subnet_of(_MAPPED_V4):
        # IPv4 in IPv6-Schreibweise: als das IPv4-Netz behandeln, das es meint.
        net = ipaddress.IPv4Network((int(net.network_address) & 0xFFFFFFFF, net.prefixlen - 96))
    if net.version == 6:
        minimum = MIN_PREFIX_V6
    elif any(net.subnet_of(private) for private in _PRIVATE_V4):
        minimum = MIN_PREFIX_V4
    else:
        minimum = MIN_PREFIX_V4_PUBLIC
    if net.prefixlen < minimum:
        raise InvalidEntry(
            f"„{text}“ ist zu groß. Damit dürfte fast jeder Rechner seine Adresse fälschen. Trag nur die Adresse "
            f"deines Proxys ein oder ein kleines Netz (öffentliche IPv4-Netze ab /{MIN_PREFIX_V4_PUBLIC}, private "
            f"wie 10.0.0.0/8 ab /{MIN_PREFIX_V4}; IPv6 ab /{MIN_PREFIX_V6})."
        )
    if net.is_multicast or (net.num_addresses == 1 and net.network_address.is_unspecified):
        raise InvalidEntry(f"„{text}“ ist keine Adresse, von der ein Proxy kommen kann ({_EXAMPLE}).")
    return net


def format_entry(net: Network) -> str:
    """Einheitliche Schreibweise: eine einzelne Adresse ohne Praefix, ein Netz mit."""
    if net.prefixlen == net.max_prefixlen:
        return str(net.network_address)
    return str(net)


def normalize(value: object) -> list[str]:
    """Die Liste aus `PUT /settings/system.trusted_proxies`, geprueft und einheitlich geschrieben (doppelte fallen
    weg). `InvalidEntry` beim ersten Fehler."""
    if not isinstance(value, list):
        raise InvalidEntry("Erwartet wird eine Liste von Adressen oder Netzen.")
    if len(value) > MAX_ENTRIES:
        raise InvalidEntry(f"Höchstens {MAX_ENTRIES} Einträge.")
    out: list[str] = []
    for raw in value:
        entry = format_entry(parse_entry(raw))
        if entry not in out:
            out.append(entry)
    return out


def lenient(values: Iterable[object], *, origin: str, warn: bool = True) -> list[str]:
    """Wie `normalize`, aber ohne Fehler: ungueltige Eintraege fallen mit einer Warnung im Protokoll weg (`warn`), nach
    `MAX_ENTRIES` ist Schluss. Fuer die Umgebungsvariable und gespeicherte Werte (beim Start)."""
    out: list[str] = []
    for raw in values:
        if isinstance(raw, str) and not raw.strip():
            continue
        try:
            entry = format_entry(parse_entry(raw))
        except InvalidEntry as exc:
            if warn:
                logger.warning("trusted_proxies_entry_ignored source=%s reason=%s", origin, exc)
            continue
        if entry in out:
            continue
        if len(out) >= MAX_ENTRIES:
            if warn:
                logger.warning("trusted_proxies_too_many source=%s kept=%d", origin, MAX_ENTRIES)
            break
        out.append(entry)
    return out


def env_entries(settings: Settings, *, warn: bool = True) -> list[str] | None:
    """Die Liste aus `NODVARD_DECK_TRUSTED_PROXIES`; `None`, wenn die Variable nicht gesetzt (oder leer) ist. Gewarnt
    wird nur beim Start (`warn`), nicht bei jedem Anzeigen der Einstellungen."""
    raw = settings.trusted_proxies
    if raw is None:
        return None
    return lenient(raw.split(","), origin=ENV_NAME, warn=warn)


@dataclass(frozen=True)
class TrustedProxies:
    """Der gueltige Stand: die Netze, ihre Schreibweise fuer die Anzeige und woher sie kommen."""

    networks: tuple[Network, ...] = ()
    entries: tuple[str, ...] = ()
    source: Source = "setting"

    def contains(self, ip: Address | None) -> bool:
        if ip is None:
            return False
        return any(ip.version == net.version and ip in net for net in self.networks)


_current = TrustedProxies()


def current() -> TrustedProxies:
    """Der gueltige Stand, ohne Datenbank (die Middleware fragt ihn bei jeder Anfrage)."""
    return _current


def apply(entries: Iterable[str], *, source: Source = "setting") -> TrustedProxies:
    """Ersetzt den gueltigen Stand. `entries` muss schon geprueft sein (`normalize`/`lenient`); was trotzdem nicht
    passt, faellt weg."""
    global _current
    networks: list[Network] = []
    shown: list[str] = []
    for entry in entries:
        try:
            net = parse_entry(entry)
        except InvalidEntry:
            continue
        networks.append(net)
        shown.append(format_entry(net))
    _current = TrustedProxies(networks=tuple(networks), entries=tuple(shown), source=source)
    return _current


def reset() -> None:
    """Zurueck auf „niemandem vertrauen“ (Tests)."""
    global _current
    _current = TrustedProxies()


async def load(session: AsyncSession, settings: Settings) -> TrustedProxies:
    """Beim Start: Umgebungsvariable, sonst die gespeicherte Einstellung."""
    from_env = env_entries(settings)
    if from_env is not None:
        return apply(from_env, source="env")
    stored = await settings_service.get_global(session, SETTING_KEY)
    values = stored if isinstance(stored, list) else []
    return apply(lenient(values, origin=SETTING_KEY), source="setting")
