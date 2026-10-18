"""Liest die Einstellungen; eine unbrauchbare Adresse wird ein fester Hinweis (`problem`), nie eine Anfrage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from nodvard_sdk import sha256_fingerprint

# Das Passwort liegt im Tresor unter diesem Namen (gebunden an die Adresse, siehe `settings.schema.json`).
PASSWORD_LABEL = "synology-password"
SETTINGS_HINT = "Unter Einstellungen → Erweiterungen → Synology DSM eintragen."

# Ab diesem Pfadteil beginnt die Oberfläche bzw. die Schnittstelle von DSM; wer die Adresse aus dem Browser kopiert
# (`https://nas.example:5001/webman/index.cgi`), meint den Teil davor.
UI_PATHS = ("webman", "webapi")

MAX_USERNAME = 128
MAX_FINGERPRINT_INPUT = 200

_WHITESPACE = re.compile(r"\s")


@dataclass(frozen=True)
class SynologyConfig:
    base_url: str | None  # leer, wenn keine (brauchbare) Adresse eingetragen ist
    username: str
    insecure_tls: bool
    problem: str | None = None  # fester Hinweis; dann gibt es keine Anfrage
    pin: str | None = None  # gemerkter Fingerabdruck (`AB:CD:...`), gilt statt des Schalters


def normalize_base_url(raw: Any) -> str:
    """Schneidet ab `webman`/`webapi`, Anfrageteil, Fragment und den Schrägstrich am Ende ab; `ValueError`, wenn die
    Adresse unbrauchbar ist. Ein Unterpfad davor (DSM hinter einem Proxy) bleibt."""
    text = str(raw or "").strip()
    if _WHITESPACE.search(text):
        raise ValueError(text)
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(text)
    # httpx würde Zugangsdaten in der Adresse als Basic-Anmeldung senden.
    if "@" in parts.netloc:
        raise ValueError(text)
    _ = parts.port  # wirft ValueError bei einem Port außerhalb von 0-65535
    kept: list[str] = []
    for segment in parts.path.split("/"):
        if segment.lower() in UI_PATHS:
            break
        kept.append(segment)
    return urlunsplit((parts.scheme, parts.netloc, "/".join(kept).rstrip("/"), "", ""))


def normalize_fingerprint(value: Any) -> str | None:
    """`None` für „nicht eingetragen“, sonst `AB:CD:...`; `ValueError`, wenn es kein SHA-256-Fingerabdruck ist."""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_FINGERPRINT_INPUT:
        raise ValueError("kein Fingerabdruck")
    if not value.strip():
        return None
    return sha256_fingerprint(value)


def normalize_username(value: Any) -> str:
    """Der Benutzername ohne Leerraum am Rand; leer, wenn er fehlt oder unbrauchbar ist."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) > MAX_USERNAME or any(ord(ch) < 32 for ch in text):
        return ""
    return text


def read_config(settings: dict[str, Any]) -> SynologyConfig:
    username = normalize_username(settings.get("username"))
    # Nur ein ausdrückliches „an“ schaltet die Prüfung ab; fehlt der Wert (nie gespeichert), gilt die Prüfung.
    insecure = settings.get("tls_insecure_skip_verify") is True
    if not str(settings.get("base_url") or "").strip():
        return SynologyConfig(None, username, insecure)
    try:
        base_url = normalize_base_url(settings.get("base_url"))
    except ValueError:
        return SynologyConfig(None, username, insecure, problem=(
            "Die Adresse von Synology DSM ist ungültig. Sie muss mit http:// oder https:// beginnen und darf "
            "keinen Benutzernamen und kein Passwort enthalten (beides trägst du getrennt ein)."
        ))
    # Ein ungültiger Fingerabdruck gilt nie still als „nicht prüfen“.
    try:
        pin = normalize_fingerprint(settings.get("tls_fingerprint_sha256"))
    except ValueError:
        return SynologyConfig(None, username, insecure, problem=(
            "Der Fingerabdruck des Zertifikats von Synology DSM ist ungültig (64 Zeichen aus 0–9 und A–F, gern mit "
            "Doppelpunkten). Bis er stimmt, verbindet sich Nodvard Deck nicht mit der Synology."
        ))
    if pin is not None and not base_url.startswith("https://"):
        return SynologyConfig(None, username, insecure, problem=(
            "Ein Fingerabdruck gilt nur für https://. Die Adresse von Synology DSM beginnt mit http:// – trage dort die "
            "https-Adresse ein (meist Port 5001) oder lösche den Fingerabdruck."
        ))
    return SynologyConfig(base_url, username, insecure, pin=pin)


# Vorgaben wie im Modul Proxmox: ab dieser Temperatur gilt ein Laufwerk als zu warm.
TEMP_WARN_DEFAULT = {"hdd": 55, "ssd": 65, "nvme": 70}
TEMP_SETTING_MIN, TEMP_SETTING_MAX = 40, 95


@dataclass(frozen=True)
class AlertConfig:
    notify: bool
    notify_update: bool
    disk_temp_warn_c: int | None  # eigene Grenze für alle Laufwerke; leer heißt Vorgabe je Art

    def disk_limit(self, kind: str) -> int:
        return self.disk_temp_warn_c or TEMP_WARN_DEFAULT.get(kind, TEMP_WARN_DEFAULT["hdd"])


def read_alerts(settings: dict[str, Any]) -> AlertConfig:
    raw = settings.get("disk_temp_warn_c")
    limit = raw if isinstance(raw, int) and not isinstance(raw, bool) and TEMP_SETTING_MIN <= raw <= TEMP_SETTING_MAX else None
    # Meldungen sind an, solange niemand sie ausdrücklich abschaltet.
    return AlertConfig(
        notify=settings.get("notify") is not False,
        notify_update=settings.get("notify_update") is not False,
        disk_temp_warn_c=limit,
    )
