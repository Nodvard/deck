"""Kennung und feste Namen von Nodvard Shield an einer Stelle.

Die Erweiterung hiess bis 0.6 `nexus-soc` (`legacy_ids` im Manifest). Was neu entsteht, traegt die heutige
Kennung (Adressen, Logger, Meldungs-Links, Audit-Namen). Was dauerhaft gespeichert liegt und woran ein Rueckweg
aufs alte Image haengt, behaelt seinen Namen -- siehe `ACTION_PREFIX`."""

from __future__ import annotations

EXT_ID = "shield"
"""Heutige Kennung (`extension.toml`)."""

SOC_PATH = f"/ext/{EXT_ID}/soc"
"""Seite der Erweiterung im Dashboard; Meldungen und Push-Links bauen ihren Pfad daraus. Gespeicherte Meldungen
und Lesezeichen mit der alten Kennung im Pfad leitet das Frontend weiter."""

LOGGER = f"nodvard_deck.ext.{EXT_ID}"
"""Name des Loggers der Erweiterung; Untermodule haengen `.<name>` an."""

ACTION_PREFIX = "nexus_soc"
"""Vorsilbe der Aktionsarten (`nexus_soc.upgrade` ...). Bewusst alt: gespeicherte Vorschlaege und Freigaben
tragen diesen Namen, und das Gate findet den Ausfuehrer nur ueber ihn -- offene Vorschlaege bleiben so ueber
ein Update und einen Rueckweg ausfuehrbar. Die Oberflaeche zeigt statt der Art das Label der `ActionSpec`."""

ACTION_RESTORE = f"{ACTION_PREFIX}.restore"
ACTION_DELETE = f"{ACTION_PREFIX}.delete"
ACTION_INSTALL = f"{ACTION_PREFIX}.install"
ACTION_UPGRADE = f"{ACTION_PREFIX}.upgrade"
ACTION_REBOOT = f"{ACTION_PREFIX}.reboot"
ACTION_BAN = f"{ACTION_PREFIX}.ban"
