"""Liest die Messwerte von `GET /metrics` (Prometheus-Textformat) nachsichtig: unverständliche Zeilen werden übersprungen."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

_NAME = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]*")
_LABEL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ESCAPES = {"n": "\n", "\\": "\\", '"': '"'}

# Je Monitor vier Reihen mit denselben Labels, z. B. `monitor_status{monitor_name="Router",monitor_type="ping"} 1`.
WANTED = frozenset({"monitor_status", "monitor_response_time", "monitor_cert_days_remaining", "monitor_cert_is_valid"})

# monitor_status: 1 oben, 0 ausgefallen, 2 ausstehend, 3 Wartung.
STATUS_NAMES = {1: "up", 0: "down", 2: "pending", 3: "maintenance"}


@dataclass
class Sample:
    name: str
    labels: dict[str, str]
    value: float | None  # None bei NaN und unendlich: nicht gemessen


def _to_float(token: str) -> float | None:
    """`ValueError`, wenn das gar keine Zahl ist; `None` bei NaN/Inf."""
    value = float(token)
    return value if math.isfinite(value) else None


def _parse_labels(line: str, i: int) -> tuple[dict[str, str], int] | None:
    """Liest die Labels ab Stelle `i` (hinter der öffnenden Klammer); `None`, wenn die Zeile kaputt ist.

    Werte dürfen `}`, `,` und maskierte Zeichen enthalten. Ergebnis sind die Labels und die Stelle hinter der Klammer."""
    labels: dict[str, str] = {}
    n = len(line)
    while True:
        while i < n and line[i] in " \t,":
            i += 1
        if i >= n:
            return None
        if line[i] == "}":
            return labels, i + 1
        match = _LABEL_NAME.match(line, i)
        if not match:
            return None
        key = match.group()
        i = match.end()
        while i < n and line[i] in " \t":
            i += 1
        if i >= n or line[i] != "=":
            return None
        i += 1
        while i < n and line[i] in " \t":
            i += 1
        if i >= n or line[i] != '"':
            return None
        i += 1
        buf: list[str] = []
        while i < n:
            char = line[i]
            if char == "\\" and i + 1 < n:
                following = line[i + 1]
                buf.append(_ESCAPES.get(following, "\\" + following))
                i += 2
                continue
            if char == '"':
                break
            buf.append(char)
            i += 1
        else:
            return None  # kein schließendes Anführungszeichen
        i += 1
        labels[key] = "".join(buf)


def parse_line(line: str) -> Sample | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    match = _NAME.match(line)
    if not match:
        return None
    name = match.group()
    i = match.end()
    labels: dict[str, str] = {}
    if i < len(line) and line[i] == "{":
        parsed = _parse_labels(line, i + 1)
        if parsed is None:
            return None
        labels, i = parsed
    rest = line[i:].split()
    if not rest or len(rest) > 2:
        return None
    try:
        value = _to_float(rest[0])
    except ValueError:
        return None
    return Sample(name, labels, value)


def parse_prometheus(text: str, *, wanted: frozenset[str] = WANTED) -> tuple[list[Sample], int]:
    """Die Reihen mit gesuchtem Namen und die Zahl ALLER gelesenen Reihen (0: vermutlich keine Messwert-Seite)."""
    kept: list[Sample] = []
    total = 0
    for line in text.splitlines():
        sample = parse_line(line)
        if sample is None:
            continue
        total += 1
        if sample.name in wanted:
            kept.append(sample)
    return kept, total


@dataclass
class MonitorData:
    name: str
    id: str | None = None
    type: str | None = None
    url: str | None = None
    status: int | None = None
    response_ms: int | None = None
    cert_days_left: int | None = None
    cert_valid: bool | None = None


def label_value(labels: dict[str, str], key: str) -> str | None:
    """Fehlend, leer und der Text `null` heißen alle "nicht vorhanden"."""
    value = labels.get(key)
    if value is None:
        return None
    value = value.strip()
    return None if not value or value == "null" else value


def safe_url(url: str | None) -> str | None:
    """Adresse ohne Benutzer, Passwort, Suchteil und Anker, denn Monitor-Adressen enthalten gern Zugangsdaten oder Schlüssel."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if not parts.scheme or not host:
            return None
        netloc = f"[{host}]" if ":" in host else host
        if parts.port:
            netloc += f":{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    except ValueError:
        return None


def _target(labels: dict[str, str]) -> str | None:
    """Ziel eines Monitors für die Anzeige: seine Adresse, sonst `Rechner:Port`."""
    url = safe_url(label_value(labels, "monitor_url"))
    if url:
        return url
    host = label_value(labels, "monitor_hostname")
    if not host:
        return None
    port = label_value(labels, "monitor_port")
    return f"{host}:{port}" if port else host


def _int(value: float | None) -> int | None:
    return None if value is None else round(value)


def collect_monitors(samples: list[Sample]) -> list[MonitorData]:
    """Führt die vier Reihen je Monitor zusammen."""
    by_key: dict[str, MonitorData] = {}
    for sample in samples:
        if sample.name not in WANTED:
            continue
        labels = sample.labels
        name = label_value(labels, "monitor_name")
        monitor_id = label_value(labels, "monitor_id")
        if name is None and monitor_id is None:
            continue
        # Ältere Versionen liefern keine `monitor_id`: dann gilt der Name samt den anderen `monitor_*`-Labels,
        # damit gleichnamige Monitore getrennt bleiben.
        if monitor_id is not None:
            key = f"id:{monitor_id}"
        else:
            rest = "|".join(f"{k}={v}" for k, v in sorted(labels.items()) if k.startswith("monitor_") and k != "monitor_name")
            key = f"name:{name}|{rest}"
        monitor = by_key.get(key)
        if monitor is None:
            monitor = by_key[key] = MonitorData(
                name=name or f"Monitor {monitor_id}",
                id=monitor_id,
                type=label_value(labels, "monitor_type"),
                url=_target(labels),
            )
        value = sample.value
        if sample.name == "monitor_status":
            monitor.status = _int(value)
        elif sample.name == "monitor_response_time":
            # Eine negative Antwortzeit heißt: nicht gemessen.
            monitor.response_ms = None if value is None or value < 0 else _int(value)
        elif sample.name == "monitor_cert_days_remaining":
            monitor.cert_days_left = _int(value)
        elif sample.name == "monitor_cert_is_valid":
            monitor.cert_valid = None if value is None else value >= 0.5
    return list(by_key.values())
