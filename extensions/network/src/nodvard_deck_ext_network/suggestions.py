"""Vorschlaege fuer App-Kacheln aus dem Nginx Proxy Manager (`nodvard_sdk.AppSuggestionProvider`).

Jeder eingeschaltete Proxy-Host wird mit seinem ersten Namen vorgeschlagen: `https://`, wenn er ein Zertifikat hat,
sonst `http://`. Platzhalter-Namen (`*.example.org`) fallen weg, sie sind keine Adresse. Ohne eingerichteten Nginx
Proxy Manager gibt es keine Vorschlaege (kein Fehler). Uebernommen wird ein Vorschlag erst, wenn jemand ihn als App
speichert; der Kern prueft die Adresse dabei wie jede andere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nodvard_sdk import AppSuggestion

from .config import NotConfigured

if TYPE_CHECKING:
    from .service import NetworkService

SOURCE = "Nginx Proxy Manager"
MAX_SUGGESTIONS = 200


def suggestions_from_hosts(hosts: list[dict]) -> list[AppSuggestion]:
    out: list[AppSuggestion] = []
    for host in hosts:
        if not isinstance(host, dict) or not host.get("enabled"):
            continue
        domains = [d for d in host.get("domains") or [] if isinstance(d, str) and d.strip() and "*" not in d]
        if not domains:
            continue
        domain = domains[0].strip().lower()
        secure = bool(host.get("certificate_id")) or bool(host.get("ssl_forced"))
        out.append(AppSuggestion(name=domain, url=f"{'https' if secure else 'http'}://{domain}", source=SOURCE))
        if len(out) >= MAX_SUGGESTIONS:
            break
    return out


class NpmAppSuggestions:
    def __init__(self, service: NetworkService) -> None:
        self._service = service

    async def app_suggestions(self) -> list[AppSuggestion]:
        try:
            client = await self._service.npm_client()
        except NotConfigured:
            return []
        return suggestions_from_hosts(await client.proxy_hosts())
