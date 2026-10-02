"""Vergleich von Zieladressen (Server-URLs).

Zugangsdaten gehoeren zu dem Server, bei dem sie eingegeben wurden. Wer eine Adresse aendert,
bekommt die Zugangsdaten nicht mit, es sei denn, es ist erkennbar derselbe Server. Diese eine
Vergleichsform benutzen der Kern (Einstellungen von Erweiterungen) und die Erweiterungen mit
eigenen Verbindungs-Routen, damit alle gleich urteilen. Die Oberflaeche rechnet dieselbe Form
(`frontend/src/lib/targetAddress.ts`); beide Seiten pruefen dieselben Beispiele
(`sdk/python/tests/vectors/same_target.json`).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


def target_form(value: Any) -> Any:
    """Vergleichsform einer Zieladresse.

    Gleich bleiben: Gross-/Kleinschreibung von Schema und Rechnername, der Standardport
    (`:80` bei http, `:443` bei https), Schraegstriche am Ende des Pfads, ein Fragment (`#...`)
    und ein fehlendes Schema (`pi.hole` gilt wie `http://pi.hole`). NICHT gleich sind ein
    anderer Pfad, eine andere Abfrage (`?...`) oder andere Anmeldedaten in der Adresse. Was
    sich nicht als Adresse lesen laesst, bleibt, wie es ist (nur ohne Leerraum an den Raendern),
    und gilt damit im Zweifel als verschieden.

    Leere Werte (`None`, leerer Text) ergeben `""`; andere Typen als Text bleiben unveraendert.
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
