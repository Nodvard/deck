"""Vergleich von Zieladressen (Server-URLs): Zugangsdaten bleiben nur bei erkennbar demselben Server.

Der Kern (Einstellungen von Erweiterungen) und Erweiterungen mit eigenen Verbindungs-Routen benutzen diese eine Form.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


# Die Oberfläche rechnet dieselbe Form (`frontend/src/lib/targetAddress.ts`); beide Seiten prüfen dieselben Beispiele
# (`sdk/python/tests/vectors/same_target.json`).
# Gleich bleiben: Groß-/Kleinschreibung von Schema und Rechnername, der Standardport (`:80` bei http, `:443` bei
# https), Schrägstriche am Ende des Pfads, ein Fragment (`#...`) und ein fehlendes Schema (`pi.hole` gilt wie
# `http://pi.hole`). NICHT gleich: ein anderer Pfad, eine andere Abfrage (`?...`) oder andere Anmeldedaten in der
# Adresse. Was sich nicht als Adresse lesen lässt, bleibt (nur ohne Leerraum an den Rändern) und gilt im Zweifel als
# verschieden.
def target_form(value: Any) -> Any:
    """Vergleichsform einer Zieladresse; gleiche Form heißt: dasselbe Ziel.

    Leere Werte (`None`, leerer Text) ergeben `""`; andere Typen als Text bleiben unverändert.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text if "://" in text else f"http://{text}")
        host = parts.hostname
        port = parts.port
    except ValueError:
        return text
    if not parts.scheme or not host:
        return text
    userinfo = parts.netloc.rpartition("@")[0] + "@" if "@" in parts.netloc else ""
    shown_host = f"[{host}]" if ":" in host else host
    shown_port = f":{port}" if port is not None and port != _DEFAULT_PORTS.get(parts.scheme) else ""
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme}://{userinfo}{shown_host}{shown_port}{parts.path.rstrip('/')}{query}"


def same_target(old: Any, new: Any) -> bool:
    """`True`, wenn beide Adressen dasselbe Ziel meinen (siehe `target_form`)."""
    return bool(target_form(old) == target_form(new))
