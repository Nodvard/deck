"""Vertrauenswürdige Proxys (`system.trusted_proxies`): Nur wenn die direkte Gegenstelle in der Liste steht, glaubt
das Dashboard `X-Forwarded-For` und `X-Forwarded-Proto`, denn diese Kopfzeilen kann jeder Client selbst schicken.
Leere Liste (Vorgabe) heißt: niemandem vertrauen."""

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
"""Länger ist keine Adresse und kein Netz (die längste IPv6-Schreibweise mit Präfix hat 50 Zeichen)."""
MIN_PREFIX_V4 = 8
"""Kleinstes Präfix für IPv4-Netze in `_PRIVATE_V4`; kein Netz darf über seinen Bereich hinausgehen."""
MIN_PREFIX_V4_PUBLIC = 24
"""Kleinstes Präfix für öffentliche IPv4-Netze: Ein Proxy steht auf einem Rechner oder in einem kleinen Netz,
schon ein /16 umfasste 65 536 fremde Adressen."""
MIN_PREFIX_V6 = 48
"""Kleinstes Präfix für IPv6, öffentlich wie intern: Ein ULA-Netz eines Standorts ist ein /48 (RFC 4193),
Container-Netze sind meist ein /64, ein /32 wäre ein ganzer Anbieter."""

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Source = Literal["setting", "env"]

# Ein Server, der auf `::` lauscht, meldet IPv4-Gegenstellen als `::ffff:192.0.2.10`; das zählt als IPv4.
_MAPPED_V4 = ipaddress.IPv6Network("::ffff:0:0/96")
_PRIVATE_V4 = tuple(
    ipaddress.IPv4Network(net)
    for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16")
)
"""Interne IPv4-Bereiche (Heimnetz, Container, CGNAT, Loopback, Link-Local): Dort sitzen nur eigene Geräte,
daher sind Netze bis zur Größe des Bereichs erlaubt."""
_EXAMPLE = "zum Beispiel 172.18.0.5 oder 172.18.0.0/28"


class InvalidEntry(ValueError):
    """Ein Eintrag ist keine erlaubte Adresse bzw. kein erlaubtes Netz; der Text ist ein fertiger Satz für die UI."""


def parse_address(text: object) -> Address | None:
    """Eine einzelne Adresse ohne Präfix und Port (IPv4 in IPv6-Schreibweise als IPv4), sonst `None`, z. B. bei einem
    Unix-Socket."""
    if not isinstance(text, str) or not text or len(text) > MAX_ENTRY_CHARS:
        return None
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    # Die Zonen-Angabe (`fe80::1%eth0`) nennt nur die Netzwerkkarte und gehört weder in die Drossel noch ins Protokoll.
    if isinstance(ip, ipaddress.IPv6Address) and ip.scope_id is not None:
        ip = ipaddress.IPv6Address(int(ip))
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _shown(raw: str) -> str:
    text = raw.strip()
    return text if len(text) <= MAX_ENTRY_CHARS else text[:MAX_ENTRY_CHARS] + "…"


def parse_entry(raw: object) -> Network:
    """Ein Eintrag als Netz (eine einzelne Adresse als /32 bzw. /128); `InvalidEntry`, wenn er nicht erlaubt ist."""
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
            # Gesetzte Host-Bits (`192.0.2.40/24`): ablehnen statt still das ganze Netz zu nehmen, gemeint war oft nur
            # der eine Rechner.
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
    # Zu große Netze sind verboten: Jeder Rechner darin dürfte seine Adresse fälschen (`0.0.0.0/0` hieße: allen trauen).
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
    # Von Multicast, `0.0.0.0` und `::` kommt keine Verbindung.
    if net.is_multicast or (net.num_addresses == 1 and net.network_address.is_unspecified):
        raise InvalidEntry(f"„{text}“ ist keine Adresse, von der ein Proxy kommen kann ({_EXAMPLE}).")
    return net


def format_entry(net: Network) -> str:
    """Einheitliche Schreibweise: eine einzelne Adresse ohne Präfix, ein Netz mit."""
    if net.prefixlen == net.max_prefixlen:
        return str(net.network_address)
    return str(net)


def normalize(value: object) -> list[str]:
    """Die Liste aus `PUT /settings/system.trusted_proxies`, geprüft und einheitlich geschrieben (Doppelte fallen weg);
    `InvalidEntry` beim ersten Fehler."""
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
    """Wie `normalize`, aber ohne Fehler: Ungültige Einträge fallen mit einer Warnung weg (`warn`), nach `MAX_ENTRIES`
    ist Schluss. Für Umgebungsvariable und gespeicherte Werte beim Start: Lieber weniger vertrauen als nicht starten."""
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
    """Die Liste aus `NODVARD_DECK_TRUSTED_PROXIES`, `None` wenn sie nicht gesetzt ist; gewarnt wird nur beim Start."""
    raw = settings.trusted_proxies
    if raw is None:
        return None
    return lenient(raw.split(","), origin=ENV_NAME, warn=warn)


# Ohne Proxy-Kopfzeilen wäre die Gegenstelle für Login-Drossel und Protokoll immer der Proxy, und hinter HTTPS
# bekäme das Anmelde-Cookie kein `Secure`.
@dataclass(frozen=True)
class TrustedProxies:
    """Der gültige Stand: die Netze, ihre Schreibweise für die Anzeige und woher sie kommen."""

    networks: tuple[Network, ...] = ()
    entries: tuple[str, ...] = ()
    source: Source = "setting"

    def contains(self, ip: Address | None) -> bool:
        if ip is None:
            return False
        return any(ip.version == net.version and ip in net for net in self.networks)


_current = TrustedProxies()


def current() -> TrustedProxies:
    """Der gültige Stand, ohne Datenbank (die Middleware fragt ihn bei jeder Anfrage)."""
    return _current


def apply(entries: Iterable[str], *, source: Source = "setting") -> TrustedProxies:
    """Ersetzt den gültigen Stand; `entries` sind schon geprüft (`normalize`/`lenient`), was nicht passt, fällt weg."""
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
    """Zurück auf „niemandem vertrauen“ (Tests)."""
    global _current
    _current = TrustedProxies()


async def load(session: AsyncSession, settings: Settings) -> TrustedProxies:
    """Beim Start: Die Umgebungsvariable hat Vorrang, sonst gilt die gespeicherte Einstellung."""
    from_env = env_entries(settings)
    if from_env is not None:
        return apply(from_env, source="env")
    stored = await settings_service.get_global(session, SETTING_KEY)
    values = stored if isinstance(stored, list) else []
    return apply(lenient(values, origin=SETTING_KEY), source="setting")
