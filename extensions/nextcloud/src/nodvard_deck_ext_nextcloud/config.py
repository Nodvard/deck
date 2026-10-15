"""Baut einen `NextcloudConnector` frisch aus den aktuellen Einstellungen +
Vault-Secret -- wie `nodvard_deck_ext_proxmox.config.build_connector()`, KEIN einmalig in
`setup()` gebauter, dauerhaft gehaltener Connector.

**Unterschied zu proxmox:** gibt `None` zurueck statt zu werfen, wenn unkonfiguriert
-- `NextcloudFileSourceProvider.file_sources()` (siehe `capabilities.py`) will eine
unkonfigurierte Extension einfach STILL aus dem Dateimanager weglassen, nicht bei
jeder `GET /files/sources`-Anfrage eine Exception durchreichen."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from .connector import NextcloudConnector

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

PASSWORD_LABEL = "nextcloud-app-password"
TOKEN_LABEL = "nextcloud-serverinfo-token"  # optional, liest nur die Kennzahlen
SETTINGS_HINT = "Unter Einstellungen → Erweiterungen → Nextcloud eintragen."


async def build_connector(ctx: "ExtensionContext") -> NextcloudConnector | None:
    settings = await ctx.settings.get()
    base_url = settings.get("base_url")
    username = settings.get("username")
    if not base_url or not username:
        return None
    if not await ctx.secrets.exists(PASSWORD_LABEL):
        return None

    handle = await ctx.secrets.get_handle(PASSWORD_LABEL)
    async with ctx.vault_use(handle) as password:
        return NextcloudConnector(
            ctx,
            base_url=str(base_url),
            username=str(username),
            password=password,
            tls_insecure_skip_verify=bool(settings.get("tls_insecure_skip_verify", False)),
        )


@dataclass(frozen=True)
class ServerInfoConfig:
    """Einstellungen für die Kennzahlen; bei einem `problem` (unbrauchbare Adresse) gibt es keine Anfrage."""

    base_url: str | None
    username: str | None
    insecure_tls: bool
    problem: str | None = None


def normalize_base_url(raw: Any) -> str:
    """Entfernt den Schrägstrich am Ende; `ValueError`, wenn die Adresse unbrauchbar ist."""
    text = str(raw or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(text)
    # httpx würde Zugangsdaten in der Adresse als Basic-Anmeldung senden, auch bei der Anmeldung mit Token.
    if "@" in parts.netloc:
        raise ValueError(text)
    _ = parts.port  # wirft ValueError bei einem Port außerhalb von 0-65535
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def read_serverinfo_config(settings: dict[str, Any]) -> ServerInfoConfig:
    username = str(settings.get("username") or "").strip() or None
    insecure = settings.get("tls_insecure_skip_verify") is True
    raw = str(settings.get("base_url") or "").strip()
    if not raw:
        return ServerInfoConfig(None, username, insecure)
    try:
        return ServerInfoConfig(normalize_base_url(raw), username, insecure)
    except ValueError:
        return ServerInfoConfig(
            None, username, insecure,
            problem=(
                "Die Adresse der Nextcloud ist ungültig. Sie muss mit http:// oder https:// beginnen und darf "
                "keinen Benutzernamen und kein Passwort enthalten."
            ),
        )
