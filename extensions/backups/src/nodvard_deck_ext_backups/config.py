"""Baut die konfigurierten `ProxmoxBackupConnector`-Instanzen frisch aus den
aktuellen Einstellungen + Vault-Secrets -- wie
`nodvard_deck_ext_proxmox.config.build_connectors()`, unabhaengig dupliziert (siehe
connector.py-Docstring: backups haelt bewusst eine eigene Proxmox-Verbindung)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .connector import ProxmoxBackupConnector

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

TOKEN_LABEL = "backups-token"


def _token_label(name: str) -> str:
    return f"{TOKEN_LABEL}:{name}"


async def build_connectors(ctx: "ExtensionContext") -> dict[str, ProxmoxBackupConnector]:
    settings = await ctx.settings.get()
    connections: list[dict[str, Any]] = settings.get("connections") or []

    result: dict[str, ProxmoxBackupConnector] = {}
    for conn in connections:
        # Nachtrag (unabhaengiges Aktivieren/Deaktivieren, wie proxmox.config): eine
        # Verbindung mit enabled=False gilt als nicht konfiguriert, behaelt aber ihr
        # Token-Secret. Fehlendes Feld (aeltere Eintraege) = enabled=True.
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
            result[str(name)] = ProxmoxBackupConnector(
                ctx,
                base_url=str(base_url),
                token_id=str(token_id),
                token_secret=token_secret,
                tls_insecure_skip_verify=bool(conn.get("tls_insecure_skip_verify") or False),
            )
    return result


async def build_connector(ctx: "ExtensionContext", name: str) -> ProxmoxBackupConnector:
    connectors = await build_connectors(ctx)
    connector = connectors.get(name)
    if connector is None:
        raise RuntimeError(f"Backup-Verbindung '{name}' ist nicht (mehr) konfiguriert.")
    return connector
