"""Baut die `ProxmoxConnector`-Instanzen frisch aus den aktuellen Einstellungen und Tresor-Geheimnissen."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .connector import ProxmoxConnector

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

# Jede Verbindung hat ihr eigenes Token-Geheimnis unter "proxmox-token:<name>".
TOKEN_LABEL = "proxmox-token"


def _token_label(name: str) -> str:
    return f"{TOKEN_LABEL}:{name}"


def foreign_secret_text(label: str) -> str:
    """Satz für ein Geheimnis unter dem Label einer Verbindung, das nicht von dieser Erweiterung stammt."""
    return (
        f"Im Tresor liegt schon ein Geheimnis „{label}“, das nicht von Proxmox VE stammt (etwa von Hand angelegt). "
        "Nodvard Deck ändert und löscht es nicht – nimm einen anderen Kurznamen oder lösch das Geheimnis selbst über "
        "die Tresor-Schnittstelle (DELETE /api/v1/secrets/{id})."
    )


async def build_connectors(ctx: "ExtensionContext") -> dict[str, ProxmoxConnector]:
    """Liest Einstellungen und Token bei jedem Aufruf neu, damit Änderungen ohne Neuaktivierung gelten.
    Unvollständige und deaktivierte Verbindungen werden still übersprungen."""
    settings = await ctx.settings.get()
    connections: list[dict[str, Any]] = settings.get("connections") or []

    result: dict[str, ProxmoxConnector] = {}
    for conn in connections:
        # Deaktivierte Verbindungen verschwinden überall, wo build_connectors() die Grundlage ist (Discovery, health(),
        # Aktionen), ohne dass ihr Token-Geheimnis verloren geht. Fehlt das Feld (ältere Verbindungen), gilt enabled=True.
        if conn.get("enabled") is False:
            continue
        name = conn.get("name")
        base_url = conn.get("base_url")
        token_id = conn.get("token_id")
        if not name or not base_url or not token_id:
            continue
        label = _token_label(str(name))
        if not await ctx.secrets.exists(label):
            continue
        handle = await ctx.secrets.get_handle(label)
        async with ctx.vault_use(handle) as token_secret:
            result[str(name)] = ProxmoxConnector(
                ctx,
                base_url=str(base_url),
                token_id=str(token_id),
                token_secret=token_secret,
                tls_insecure_skip_verify=bool(conn.get("tls_insecure_skip_verify") or False),
                # Roh weiter: der Connector prüft ihn, ein ungültiger sperrt die Verbindung (nie "nicht prüfen").
                tls_fingerprint_sha256=conn.get("tls_fingerprint_sha256"),
            )
    return result


class ConnectionMissing(RuntimeError):
    """Die Verbindung gibt es nicht (mehr). `readable`: der Text ist ein Satz der Erweiterung und darf so in die
    Oberfläche (siehe `ProxmoxApiError`)."""

    readable = True


async def build_connector(ctx: "ExtensionContext", name: str) -> ProxmoxConnector:
    """Baut nur die Verbindung `name`, für Aufrufer, die schon eine `provider_ref` haben."""
    connectors = await build_connectors(ctx)
    connector = connectors.get(name)
    if connector is None:
        raise ConnectionMissing(f"Proxmox-Verbindung '{name}' ist nicht (mehr) konfiguriert.")
    return connector
