"""Vorschläge für App-Kacheln aus den eingeschalteten Proxy-Hosts des Nginx Proxy Managers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nodvard_sdk import AppSuggestion

from .config import NotConfigured

if TYPE_CHECKING:
    from .service import NetworkService

SOURCE = "Nginx Proxy Manager"
MAX_SUGGESTIONS = 200


def suggestions_from_hosts(hosts: list[dict]) -> list[AppSuggestion]:
    # Erster Name des Hosts, `https://` bei Zertifikat, sonst `http://`. Platzhalter (`*.example.org`) sind keine
    # Adresse. Übernommen wird ein Vorschlag erst, wenn jemand ihn als App speichert; der Kern prüft ihn dann wie jede.
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
