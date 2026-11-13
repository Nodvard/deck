"""Kennung und feste Namen von Nodvard Shield."""

from __future__ import annotations

EXT_ID = "shield"
"""Kennung der Erweiterung (`extension.toml`)."""

SOC_PATH = f"/ext/{EXT_ID}/soc"
"""Seite der Erweiterung im Dashboard; Meldungen und Push-Links bauen ihren Pfad daraus.
Das Frontend leitet Pfade mit der alten Kennung weiter."""

LOGGER = f"nodvard_deck.ext.{EXT_ID}"
"""Logger-Name der Erweiterung; Untermodule hängen `.<name>` an."""

ACTION_PREFIX = "nexus_soc"
"""Feste Vorsilbe, über die das Gate gespeicherte Vorschläge und Freigaben ihrem Ausführer zuordnet."""

ACTION_RESTORE = f"{ACTION_PREFIX}.restore"
ACTION_DELETE = f"{ACTION_PREFIX}.delete"
ACTION_INSTALL = f"{ACTION_PREFIX}.install"
ACTION_UPGRADE = f"{ACTION_PREFIX}.upgrade"
ACTION_REBOOT = f"{ACTION_PREFIX}.reboot"
ACTION_BAN = f"{ACTION_PREFIX}.ban"
ACTION_REALTIME = f"{ACTION_PREFIX}.realtime"
ACTION_FAIL2BAN_CONFIG = f"{ACTION_PREFIX}.fail2ban_config"
