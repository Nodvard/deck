"""Vorlagen für neue Stacks: ein kleiner Katalog mit fester Image-Version und einem Hinweis, was danach zu tun ist."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from fastapi import APIRouter


@dataclass(frozen=True)
class Template:
    id: str
    """Zugleich der vorgeschlagene Name des Stacks."""
    name: str
    summary: str
    compose: str
    note: str
    ports: tuple[str, ...]
    """Die Ports auf dem Server, damit die Oberfläche sie vorab nennen kann."""


TEMPLATES: tuple[Template, ...] = (
    Template(
        id="uptime-kuma",
        name="Uptime Kuma",
        summary="Überwacht Webseiten und Dienste und meldet, wenn etwas ausfällt.",
        compose="""services:
  uptime-kuma:
    image: louislam/uptime-kuma:2.5.6
    restart: unless-stopped
    ports:
      - "3001:3001"
    volumes:
      - daten:/app/data

volumes:
  daten:
""",
        note="Öffne gleich nach dem Start Port 3001, wähle die Datenbank (SQLite reicht) und leg dein Konto an. Bis dahin kann das jeder im "
        "Netz tun.",
        ports=("3001",),
    ),
    Template(
        id="vaultwarden",
        name="Vaultwarden",
        summary="Passwort-Tresor, den die Bitwarden-Apps und -Erweiterungen nutzen können.",
        compose="""services:
  vaultwarden:
    image: vaultwarden/server:1.37.4
    restart: unless-stopped
    environment:
      # Nach dem ersten eigenen Konto auf "false" stellen, damit sich niemand Fremdes anmeldet.
      SIGNUPS_ALLOWED: "true"
    ports:
      - "8081:80"
    volumes:
      - daten:/data

volumes:
  daten:
""",
        note="Leg gleich nach dem Start dein Konto an und stell danach SIGNUPS_ALLOWED auf \"false\"; bis dahin kann sich jeder im Netz "
        "ein Konto anlegen. Der Tresor im Browser und die Apps brauchen HTTPS, etwa über Nginx Proxy Manager.",
        ports=("8081",),
    ),
    Template(
        id="nginx-proxy-manager",
        name="Nginx Proxy Manager",
        summary="Leitet Adressen an deine Dienste weiter und holt HTTPS-Zertifikate von Let's Encrypt.",
        compose="""services:
  nginx-proxy-manager:
    image: jc21/nginx-proxy-manager:2.16.0
    restart: unless-stopped
    environment:
      # Ohne IPv6 im Container startet nginx sonst nicht; Anfragen über IPv6 leitet Docker trotzdem weiter.
      DISABLE_IPV6: "true"
    ports:
      - "80:80"
      - "443:443"
      - "81:81"
    volumes:
      - daten:/data
      - zertifikate:/etc/letsencrypt

volumes:
  daten:
  zertifikate:
""",
        note="Belegt die Ports 80 und 443, dort darf auf dem Server nichts anderes laufen. Öffne gleich nach dem Start die Verwaltung unter "
        "Port 81 und leg dein Konto an; bis dahin kann das jeder im Netz tun.",
        ports=("80", "443", "81"),
    ),
    Template(
        id="gitea",
        name="Gitea",
        summary="Eigener Git-Server mit Weboberfläche, Tickets und Pull-Requests.",
        compose="""services:
  gitea:
    image: gitea/gitea:28.1.0
    restart: unless-stopped
    environment:
      USER_UID: "1000"
      USER_GID: "1000"
    ports:
      - "3000:3000"
      - "2222:22"
    volumes:
      - daten:/data

volumes:
  daten:
""",
        note="Öffne gleich nach dem Start Port 3000 und schließ die Einrichtung ab (SQLite reicht für den Anfang); bis dahin kann sie jeder "
        "im Netz übernehmen. Trag dort als SSH-Port 2222 ein, damit die Adressen zum Klonen stimmen.",
        ports=("3000", "2222"),
    ),
)


def template_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/stack-templates")
    async def stack_templates() -> dict:
        """Die Vorlagen für „Neuer Stack“: Name, Beschreibung, Hinweis, Ports und die Compose-Datei."""
        return {"templates": [asdict(t) for t in TEMPLATES]}

    return router
