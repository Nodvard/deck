"""Baut die konfigurierten `ProxmoxConnector`-Instanzen frisch aus den aktuellen
Einstellungen + Vault-Secrets -- eine pro benannter Verbindung.

Wie `_NtfyChannel._config()`/`_auth_header()` in der ntfy-Extension (WP-6): KEIN
einmalig in `setup()` gebauter, dauerhaft gehaltener Connector -- Einstellungen und
Token koennen sich aendern, ohne dass die Extension neu aktiviert wird, also liest
jeder Aufruf sie neu. Das kostet einen zusaetzlichen DB-/Vault-Roundtrip pro Aufruf,
ist aber derselbe Tausch, den ntfy schon eingeht.

**Multi-Instanz-Nachtrag:** `ExtensionRecord.settings` ist laut Datenmodell (WP-3)
EIN JSON-Blob pro Extension-ID -- keine eingebaute Mehrfachkonfiguration. Statt den
Kern dafuer zu aendern (neues Datenmodell, neue Runtime-Semantik, betrifft JEDE
Extension), traegt `settings.connections` jetzt eine LISTE benannter Proxmox-
Verbindungen (`{name, base_url, token_id, tls_insecure_skip_verify}`) -- pve1 UND
pve2 gleichzeitig, ohne dass der Kern von "mehreren Instanzen einer Extension"
je erfaehrt. Jede Verbindung bekommt ihr eigenes Token-Secret unter dem Label
`f"{TOKEN_LABEL}:{name}"` (siehe `POST .../connections/{name}/token` in
`__init__.py`). Eine unvollstaendig konfigurierte Verbindung (Name ohne Token o.ae.)
wird still uebersprungen, nicht als Fehler behandelt -- dieselbe Haltung wie
nextclouds `build_connector()` bei fehlender Konfiguration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .connector import ProxmoxConnector

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

TOKEN_LABEL = "proxmox-token"


def _token_label(name: str) -> str:
    return f"{TOKEN_LABEL}:{name}"


async def build_connectors(ctx: "ExtensionContext") -> dict[str, ProxmoxConnector]:
    settings = await ctx.settings.get()
    connections: list[dict[str, Any]] = settings.get("connections") or []

    result: dict[str, ProxmoxConnector] = {}
    for conn in connections:
        # Nachtrag (unabhaengiges Aktivieren/Deaktivieren): eine Verbindung mit
        # enabled=False wird wie eine nicht konfigurierte behandelt -- sie
        # verschwindet dadurch ueberall, wo build_connectors() die Grundlage ist
        # (Discovery, health(), Aktionen), OHNE ihr Token-Secret zu verlieren.
        # Fehlt das Feld (aeltere, vor diesem Nachtrag angelegte Verbindungen),
        # gilt enabled=True -- rueckwaertskompatibel.
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
            )
    return result


async def build_connector(ctx: "ExtensionContext", name: str) -> ProxmoxConnector:
    """Fuer Aufrufer, die bereits eine `provider_ref` (und damit einen
    Verbindungsnamen) haben -- baut NUR diese eine Verbindung, statt alle."""
    connectors = await build_connectors(ctx)
    connector = connectors.get(name)
    if connector is None:
        raise RuntimeError(f"Proxmox-Verbindung '{name}' ist nicht (mehr) konfiguriert.")
    return connector
