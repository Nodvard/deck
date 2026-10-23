"""Die Kacheln der Startseite: je Gerät drei (Zustand, Speicher, Hyper Backup), angelegt nach den Einstellungen."""

from __future__ import annotations

from nodvard_sdk import Badge, GridSize, ListItem, ListView, Refresh, WidgetSpec

from .config import LEGACY_NAME, DeviceConfig
from .transport import SERVICE

_ITEM = ListItem(title="{{ title }}", subtitle="{{ subtitle }}", badge=Badge(text="{{ label }}", tone="{{ tone }}"))

# (Art, Titel, Symbol, Gruppe auf der Startseite, Abstand der Abfrage in Sekunden)
KINDS = (
    ("status", SERVICE, "server", None, 60),
    ("volumes", "Synology Speicher", "server", None, 60),
    ("backups", "Synology Hyper Backup", "database-backup", "backups", 300),
)


def tile_id(kind: str, name: str) -> str:
    """Das übernommene Gerät behält die alten Kennungen, damit die Kacheln auf der Startseite an ihrem Platz bleiben."""
    return kind if name == LEGACY_NAME else f"{kind}-{name}"


def _spec(title: str, icon: str, group: str | None, interval_s: int, *, spec_id: str, endpoint: str) -> WidgetSpec:
    return WidgetSpec(
        id=spec_id, title=title, group=group, icon=icon, size=GridSize(w=2, h=2), refresh=Refresh(interval_s=interval_s),
        data_endpoint=endpoint, permissions=["hosts.read"], view=ListView(item=_ITEM, empty_text="Keine Daten"),
    )


def tile_specs(configs: list[DeviceConfig]) -> list[WidgetSpec]:
    """Die Kacheln zu den Geräten; ohne brauchbares Gerät bleiben die drei Kacheln da und melden „nicht eingerichtet“,
    damit man sie unter „Widgets verwalten“ findet."""
    named = [c for c in configs if c.named]
    if not named:
        return [_spec(title, icon, group, interval, spec_id=kind, endpoint=f"widgets/{kind}") for kind, title, icon, group, interval in KINDS]
    many = len(configs) > 1
    return [
        _spec(
            f"{title} · {cfg.name}" if many else title, icon, group, interval,
            spec_id=tile_id(kind, cfg.name), endpoint=f"widgets/{kind}/{cfg.name}",
        )
        for cfg in named
        for kind, title, icon, group, interval in KINDS
    ]
