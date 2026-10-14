"""Spiel-Profile: Was ein Gameserver kann, ist für jedes Spiel gleich, wie man es auf dem Host abfragt, steckt im Profil.

Ein neues Spiel ist ein neues Modul hier; die Werte je Server (`servers.<host_id>`) fallen auf die Profil-Defaults zurück.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ServerConfig:
    profile: str
    values: dict[str, str] = field(default_factory=dict)

    def get(self, key: str, default: str = "") -> str:
        return str(self.values.get(key) or default)


@dataclass
class ConfigField:
    key: str
    label: str
    help: str
    default: str = ""


class GameProfile(Protocol):
    id: str
    label: str
    fields: list[ConfigField]

    def status_command(self, config: ServerConfig) -> str:
        """Ein Befehl (per SSH), der den kompletten Zustand als JSON auf stdout schreibt."""
        ...

    def parse_status(self, stdout: str) -> dict[str, Any]:
        """JSON -> einheitliches Status-Objekt (Felder siehe `ValheimWindowsProfile.parse_status`)."""
        ...

    def action_command(self, action: str, config: ServerConfig) -> str:
        """`action` in {"start", "stop", "restart", "backup"}."""
        ...


def _registry() -> dict[str, GameProfile]:
    from .valheim import ValheimWindowsProfile

    profiles: list[GameProfile] = [ValheimWindowsProfile()]
    return {p.id: p for p in profiles}


PROFILES = _registry()
DEFAULT_PROFILE = "valheim-windows"


def get_profile(profile_id: str | None) -> GameProfile:
    return PROFILES.get(profile_id or DEFAULT_PROFILE) or PROFILES[DEFAULT_PROFILE]


def config_for(settings: dict[str, Any], host_id: str) -> ServerConfig:
    """Eintrag des Servers aus den Einstellungen; der alte globale `service_name` gilt als Rückfall, damit bestehende
    Installationen weiterlaufen."""
    entry = dict((settings.get("servers") or {}).get(host_id) or {})
    profile = str(entry.pop("profile", "") or DEFAULT_PROFILE)
    if settings.get("service_name") and not entry.get("service_name"):
        entry["service_name"] = settings["service_name"]
    return ServerConfig(profile=profile, values={k: str(v) for k, v in entry.items() if v not in (None, "")})
