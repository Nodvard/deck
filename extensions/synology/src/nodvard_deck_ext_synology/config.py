"""Liest die Geräte aus den Einstellungen; ein unbrauchbarer Eintrag bekommt ein `problem`, die übrigen laufen weiter."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from nodvard_sdk import sha256_fingerprint

# Das Passwort liegt je Gerät im Tresor unter `synology-password:<Kurzname>` (gebunden an die Adresse, siehe
# `settings.schema.json`). Ohne Zusatz hieß der Eintrag, als es nur ein Gerät gab.
PASSWORD_LABEL = "synology-password"
SETTINGS_HINT = "Unter Einstellungen → Erweiterungen → Synology DSM eintragen."

# So heißt das Gerät, das aus den früheren Einzelfeldern übernommen wird. Es behält die alten Kennungen (Kacheln,
# Meldungen, Wächter-Stand), damit nichts doppelt oder verwaist dasteht.
LEGACY_NAME = "synology"
# Die Einzelfelder aus der Zeit mit nur einem Gerät.
LEGACY_FIELDS = ("base_url", "username", "tls_insecure_skip_verify", "tls_fingerprint_sha256")

# Ab diesem Pfadteil beginnt die Oberfläche bzw. die Schnittstelle von DSM; wer die Adresse aus dem Browser kopiert
# (`https://nas.example:5001/webman/index.cgi`), meint den Teil davor.
UI_PATHS = ("webman", "webapi")

MAX_USERNAME = 128
MAX_FINGERPRINT_INPUT = 200
MAX_NAME = 40

# Der Kurzname steckt in Adressen, Dateinamen und Tresor-Labels: kein Punkt oder Strich am Anfang.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}")
_WHITESPACE = re.compile(r"\s")


def password_label(name: str) -> str:
    return f"{PASSWORD_LABEL}:{name}"


def device_of(label: str) -> str | None:
    """Der Kurzname zu einem Passwort-Label, sonst `None` (auch für den alten Eintrag ohne Kurznamen)."""
    prefix = f"{PASSWORD_LABEL}:"
    return label.removeprefix(prefix) if label.startswith(prefix) else None


def is_valid_name(name: str) -> bool:
    return _NAME.fullmatch(name) is not None


@dataclass(frozen=True)
class DeviceConfig:
    name: str
    base_url: str | None  # leer, wenn keine (brauchbare) Adresse eingetragen ist
    username: str
    insecure_tls: bool
    problem: str | None = None  # fester Hinweis; dann gibt es keine Anfrage
    pin: str | None = None  # gemerkter Fingerabdruck (`AB:CD:...`), gilt statt des Schalters
    named: bool = True  # ob der Kurzname brauchbar ist; nur dann gibt es Tresor-Eintrag, Kacheln und Wächter-Stand


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


def _read_device(name: str, entry: dict[str, Any]) -> DeviceConfig:
    """Ein Gerät mit gültigem, noch nicht vergebenem Kurznamen."""
    username = normalize_username(entry.get("username"))
    # Nur ein ausdrückliches „an“ schaltet die Prüfung ab; fehlt der Wert (nie gespeichert), gilt die Prüfung.
    insecure = entry.get("tls_insecure_skip_verify") is True
    if not str(entry.get("base_url") or "").strip():
        return DeviceConfig(name, None, username, insecure)
    try:
        base_url = normalize_base_url(entry.get("base_url"))
    except ValueError:
        return DeviceConfig(name, None, username, insecure, problem=(
            f"Die Adresse von {name} ist ungültig. Sie muss mit http:// oder https:// beginnen und darf "
            "keinen Benutzernamen und kein Passwort enthalten (beides trägst du getrennt ein)."
        ))
    # Ein ungültiger Fingerabdruck gilt nie still als „nicht prüfen“.
    try:
        pin = normalize_fingerprint(entry.get("tls_fingerprint_sha256"))
    except ValueError:
        return DeviceConfig(name, None, username, insecure, problem=(
            f"Der Fingerabdruck des Zertifikats von {name} ist ungültig (64 Zeichen aus 0–9 und A–F, gern mit "
            "Doppelpunkten). Bis er stimmt, verbindet sich Nodvard Deck nicht mit diesem Gerät."
        ))
    if pin is not None and not base_url.startswith("https://"):
        return DeviceConfig(name, None, username, insecure, problem=(
            f"Ein Fingerabdruck gilt nur für https://. Die Adresse von {name} beginnt mit http:// – trage dort die "
            "https-Adresse ein (meist Port 5001) oder lösche den Fingerabdruck."
        ))
    return DeviceConfig(name, base_url, username, insecure, pin=pin)


def read_devices(settings: dict[str, Any]) -> list[DeviceConfig]:
    raw = settings.get("connections")
    entries = [(str(e.get("name") or "").strip(), e) for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
    # Groß- und Kleinschreibung zählt hier nicht: Der Name steckt in Dateinamen.
    counts = Counter(name.casefold() for name, _ in entries if is_valid_name(name))
    result: list[DeviceConfig] = []
    for name, entry in entries:
        if not name:
            continue
        if not is_valid_name(name):
            shown = name[:MAX_NAME]
            result.append(DeviceConfig(
                shown, None, "", False, named=False, problem=(
                    f"Der Kurzname „{shown}“ ist ungültig. Erlaubt sind Buchstaben, Zahlen, Punkt, Bindestrich und "
                    f"Unterstrich (höchstens {MAX_NAME} Zeichen, am Anfang ein Buchstabe oder eine Zahl), keine Leerzeichen."
                ),
            ))
            continue
        # Alle Einträge mit dem Namen sind unbrauchbar, nicht nur die späteren: Welcher „der richtige“ wäre, bestimmt
        # sonst die Reihenfolge, und das Passwort im Tresor gehört zur Adresse eines bestimmten Eintrags.
        if counts[name.casefold()] > 1:
            result.append(DeviceConfig(
                name, None, "", False, named=False,
                problem=f"Der Kurzname „{name}“ ist mehrfach vergeben. Jedes Gerät braucht einen eigenen Kurznamen.",
            ))
            continue
        result.append(_read_device(name, entry))
    return result


def legacy_entry(settings: dict[str, Any]) -> dict[str, Any] | None:
    """Das frühere Einzelgerät als Eintrag für `connections`; `None`, wenn keine Adresse eingetragen war."""
    base_url = settings.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        return None
    entry: dict[str, Any] = {"name": LEGACY_NAME, "base_url": base_url}
    for field in LEGACY_FIELDS[1:]:
        if settings.get(field) not in (None, ""):
            entry[field] = settings[field]
    return entry


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
