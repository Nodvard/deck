"""Gegenstellen, über die Anmeldungen mit `X-Forwarded-For` kamen, die aber nicht als Proxy eingetragen sind.

Gemerkt werden nur Adresse und Zeit, nie der Inhalt der Kopfzeilen, und nur aus dem Heimnetz: Eine Adresse aus dem
Internet könnte sonst jeder mit einem Konto in den Hinweis schreiben, und wer sie einträgt, ließe sie Adressen fälschen.
Nur `X-Forwarded-For` zählt, denn nur das wertet `api/proxy_headers.py` aus."""

from __future__ import annotations

import ipaddress
import time

from .trusted_proxies import Address, TrustedProxies

HOME_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
        # Gemeinsamer Adressraum, so kommen etwa Geräte über Tailscale an.
        "100.64.0.0/10",
        "::1/128", "fc00::/7", "fe80::/10",
    )
)
KEEP_SECONDS = 7 * 86400
"""So lange zeigt der Hinweis eine Adresse nach ihrer letzten Anfrage."""
MAX_ADDRESSES = 8

_seen: dict[Address, float] = {}


def note(peer: Address | None, now: float | None = None) -> None:
    """Merkt sich `peer` mit der Zeit dieser Anfrage; Adressen außerhalb des Heimnetzes nicht."""
    if peer is None or not any(peer.version == net.version and peer in net for net in HOME_NETWORKS):
        return
    _seen.pop(peer, None)
    _seen[peer] = time.time() if now is None else now
    while len(_seen) > MAX_ADDRESSES:
        del _seen[next(iter(_seen))]


def recent(trust: TrustedProxies, now: float | None = None) -> list[tuple[str, float]]:
    """Die gemerkten Adressen der letzten 7 Tage, die noch nicht eingetragen sind, die zuletzt gesehene zuerst."""
    now = time.time() if now is None else now
    found = [(ip, at) for ip, at in _seen.items() if now - at <= KEEP_SECONDS and not trust.contains(ip)]
    return [(str(ip), at) for ip, at in sorted(found, key=lambda item: item[1], reverse=True)]


def reset() -> None:
    """Alles vergessen (Tests)."""
    _seen.clear()
