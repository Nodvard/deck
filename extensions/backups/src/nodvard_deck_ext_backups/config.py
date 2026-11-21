"""Baut die `ProxmoxBackupConnector`-Instanzen frisch aus den Einstellungen und den Tresor-Geheimnissen."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .connector import ProxmoxBackupConnector

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

TOKEN_LABEL = "backups-token"


def _token_label(name: str) -> str:
    return f"{TOKEN_LABEL}:{name}"


def stored_secret_text(label: str) -> str:
    """Satz, wenn eine Änderung gespeicherte Zugangsdaten löschen oder einem weniger geprüften Server geben würde."""
    return (
        f"Für „{label}“ liegen Zugangsdaten im Tresor. Diese Änderung würde sie löschen oder an einen Server geben, "
        "dessen Zertifikat bisher abgelehnt wurde, und braucht deshalb dein Passwort und, wenn Zwei-Faktor an ist, den "
        "Code aus der App. Ändere die Verbindung unter Einstellungen → Erweiterungen → Proxmox-Backups oder entferne dort "
        "zuerst die Zugangsdaten (API: DELETE /api/v1/secrets/{id} mit current_password)."
    )



def leftover_secret_text(name: str, label: str) -> str:
    """Satz, wenn unter dem Namen einer neuen Verbindung noch Zugangsdaten liegen."""
    return (
        f"Unter dem Namen „{name}“ liegen noch Zugangsdaten einer früheren Verbindung („{label}“). Wähle einen anderen "
        "Namen oder lege die Verbindung unter Einstellungen → Erweiterungen → Proxmox-Backups an; dort fallen die alten "
        "Zugangsdaten nach Rückfrage mit deinem Passwort weg. Stammen sie nicht von Proxmox-Backups, lassen sie sich unter "
        "Einstellungen → Tresor löschen (API: DELETE /api/v1/secrets/{id} mit current_password)."
    )

async def build_connectors(ctx: "ExtensionContext") -> dict[str, ProxmoxBackupConnector]:
    settings = await ctx.settings.get()
    connections: list[dict[str, Any]] = settings.get("connections") or []

    result: dict[str, ProxmoxBackupConnector] = {}
    for conn in connections:
        # Eine Verbindung mit enabled=False gilt als nicht konfiguriert, behält aber ihr Token-Geheimnis.
        # Fehlt das Feld (ältere Einträge), gilt enabled=True.
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
                # Roh weiter: der Connector prüft ihn, ein ungültiger sperrt die Verbindung (nie „nicht prüfen“).
                tls_fingerprint_sha256=conn.get("tls_fingerprint_sha256"),
            )
    return result


async def build_connector(ctx: "ExtensionContext", name: str) -> ProxmoxBackupConnector:
    connectors = await build_connectors(ctx)
    connector = connectors.get(name)
    if connector is None:
        raise RuntimeError(f"Backup-Verbindung '{name}' ist nicht (mehr) konfiguriert.")
    return connector
