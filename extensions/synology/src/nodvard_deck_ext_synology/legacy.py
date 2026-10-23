"""Übernimmt das Einzelgerät aus der Zeit vor den mehreren Geräten als erstes Gerät."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .config import LEGACY_NAME, PASSWORD_LABEL, legacy_entry, password_label

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

logger = logging.getLogger(__name__)

ADOPTED_NOTE = "Übernommen aus der Einrichtung mit nur einem Gerät."
# Steht in den Einstellungen, sobald die Übernahme gelaufen ist; `connections` allein reicht nicht, weil der Kern den
# Schlüssel bei `null` entfernt.
ADOPTED_MARK = "legacy_adopted"


async def _copy_password(ctx: ExtensionContext) -> None:
    """Bringt das Passwort des Geräts „synology“ auf den Stand des alten Eintrags; ohne alten Eintrag bleibt keines."""
    target = password_label(LEGACY_NAME)
    password = ""
    if await ctx.secrets.exists(PASSWORD_LABEL):
        handle = await ctx.secrets.get_handle(PASSWORD_LABEL)
        async with ctx.vault_use(handle) as value:
            password = str(value or "")
    has_target = await ctx.secrets.exists(target)
    if not password:
        if has_target:
            await ctx.secrets.delete(target)
    elif has_target:
        await ctx.secrets.replace(label=target, value=password)
    else:
        await ctx.secrets.create(label=target, kind=handle.kind, value=password, description=ADOPTED_NOTE)


async def adopt_single_device(ctx: ExtensionContext) -> bool:
    """Macht aus den alten Einzelfeldern und dem alten Passwort das Gerät „synology“, ohne die alten Werte anzutasten."""
    settings = await ctx.settings.get()
    entry = legacy_entry(settings)
    # Nur einmal: Sobald die Marke oder `connections` steht, gilt die Liste, und die alten Werte bleiben für einen Rückweg
    # auf die ältere Version stehen. Eine Passwort-Kopie ohne beides stammt von einem abgebrochenen Versuch und wird erneuert.
    if entry is None or settings.get(ADOPTED_MARK) or "connections" in settings:
        return False
    await _copy_password(ctx)
    await ctx.settings.set({**settings, "connections": [entry], ADOPTED_MARK: True})
    logger.info("Synology: das bisherige Gerät ist jetzt das erste Gerät („%s“).", LEGACY_NAME)
    return True


async def forget_legacy_password(ctx: ExtensionContext, values: dict[str, Any]) -> None:
    """Löscht den alten Tresor-Eintrag, sobald das übernommene Gerät „synology“ nicht mehr in der Liste steht."""
    if not values.get(ADOPTED_MARK):
        return
    raw = values.get("connections")
    names = {str(entry.get("name") or "").strip() for entry in raw if isinstance(entry, dict)} if isinstance(raw, list) else set()
    # Ohne dieses Gerät führt kein Rückweg mehr zur älteren Version, und im Tresor ließe sich der Eintrag nicht anfassen.
    if LEGACY_NAME not in names and await ctx.secrets.delete(PASSWORD_LABEL):
        logger.info("Synology: der alte Passwort-Eintrag ist gelöscht, das Gerät „%s“ gibt es nicht mehr.", LEGACY_NAME)
