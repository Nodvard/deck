"""Gast-Hardware aendern (Roadmap Punkt 1: "VM-Einstellungen
bearbeiten"): Kerne, Sockel, RAM, Ballon, Swap, Autostart, Startreihenfolge.

Bewusst eine WHITELIST wie beim Lesen (guest_config.py): nur diese Felder, jeweils mit
Grenzen. Alles andere (Disks, Netzwerk, PCI-Durchreichung, Cloud-Init) bleibt Proxmox
vorbehalten -- dort kann ein Tippfehler einen Gast unbootbar machen oder vom Netz
nehmen, das gehoert nicht hinter ein Formular mit fuenf Feldern.

Geschrieben wird IMMER mit dem `digest` der gerade gelesenen Konfiguration: hat jemand
anderes (Proxmox-Oberflaeche, zweiter Tab) zwischendurch geaendert, lehnt Proxmox ab,
statt dessen Aenderung stumm zu ueberschreiben.
"""

from __future__ import annotations

from typing import Any

EDITABLE: dict[str, tuple[str, ...]] = {
    "qemu": ("cores", "sockets", "memory", "balloon", "onboot", "startup_order"),
    "lxc": ("cores", "memory", "swap", "onboot", "startup_order"),
}

LABEL = {
    "cores": "Kerne", "sockets": "Sockel", "memory": "RAM", "balloon": "Ballon-Minimum",
    "swap": "Swap", "onboot": "Autostart", "startup_order": "Startreihenfolge",
}

_LIMITS = {
    "cores": (1, 512),
    "sockets": (1, 16),
    "memory": (16, 16 * 1024 * 1024),  # MB
    "balloon": (0, 16 * 1024 * 1024),
    "swap": (0, 16 * 1024 * 1024),
    "startup_order": (0, 9999),
}
_MB_FIELDS = ("memory", "balloon", "swap")


class ConfigEditError(ValueError):
    """Ungueltige Aenderung -- die Meldung geht so an den Nutzer."""


def _int(key: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ConfigEditError(f"{LABEL[key]}: ganze Zahl erwartet.")
    try:
        number = int(value)
    except ValueError as exc:
        raise ConfigEditError(f"{LABEL[key]}: ganze Zahl erwartet.") from exc
    low, high = _LIMITS[key]
    if not low <= number <= high:
        raise ConfigEditError(f"{LABEL[key]}: erlaubt sind {low} bis {high}.")
    return number


def _startup_parts(value: Any) -> dict[str, str]:
    """"order=3,up=30" -> {"order": "3", "up": "30"}; eine nackte Zahl ist die Reihenfolge."""
    parts: dict[str, str] = {}
    for chunk in str(value or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        key, sep, val = chunk.partition("=")
        if sep:
            parts[key.strip()] = val.strip()
        else:
            parts["order"] = key
    return parts


def current_values(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    """Die editierbaren Felder so, wie das Formular sie zeigt (Proxmox-Standardwerte
    eingesetzt, wo der Schluessel fehlt)."""
    values: dict[str, Any] = {
        "cores": int(config.get("cores") or 1),
        "memory": int(config.get("memory") or 512),
        "onboot": str(config.get("onboot", "0")) == "1",
    }
    order = _startup_parts(config.get("startup")).get("order")
    values["startup_order"] = int(order) if order and order.isdigit() else None
    if kind == "qemu":
        values["sockets"] = int(config.get("sockets") or 1)
        # Kein "balloon"-Schluessel heisst: Ballon aktiv, Minimum = RAM.
        values["balloon"] = int(config["balloon"]) if "balloon" in config else values["memory"]
    else:
        values["swap"] = int(config.get("swap") or 0)
    return values


def build_update(kind: str, config: dict[str, Any], changes: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Prueft `changes` gegen die Whitelist und die aktuelle Konfiguration. Liefert die
    Proxmox-Parameter fuer PUT .../config (inkl. `digest`) und die Liste der echten
    Aenderungen fuers Protokoll. Unveraenderte Werte fallen heraus."""
    if kind not in EDITABLE:
        raise ConfigEditError("Nur VMs und Container lassen sich bearbeiten.")
    if not isinstance(changes, dict) or not changes:
        raise ConfigEditError("Keine Änderung angegeben.")
    unknown = sorted(set(changes) - set(EDITABLE[kind]))
    if unknown:
        raise ConfigEditError(f"Nicht änderbar über Nodvard Deck: {', '.join(unknown)}.")

    before = current_values(kind, config)
    after = dict(before)
    for key, raw in changes.items():
        if key == "onboot":
            if not isinstance(raw, bool):
                raise ConfigEditError("Autostart: an oder aus erwartet.")
            after[key] = raw
        elif key == "startup_order":
            after[key] = None if raw is None or raw == "" else _int(key, raw)
        else:
            after[key] = _int(key, raw)

    # Ohne "balloon"-Schluessel folgt das Minimum in Proxmox dem RAM -- wer nur
    # den RAM aendert, aendert es mit. Vorher wurde RAM verkleinern faelschlich abgelehnt
    # (Minimum = alter RAM > neuer RAM), obwohl das Ballon-Feld nie angefasst wurde.
    balloon_follows = kind == "qemu" and "balloon" not in config and "balloon" not in changes
    if balloon_follows:
        after["balloon"] = after["memory"]

    if kind == "qemu" and after["balloon"] > after["memory"]:
        raise ConfigEditError("Ballon-Minimum darf nicht größer als der RAM sein.")

    params: dict[str, Any] = {}
    deletes: list[str] = []
    diff: list[dict[str, Any]] = []
    for key in EDITABLE[kind]:
        if after[key] == before[key] or (key == "balloon" and balloon_follows):
            continue
        diff.append({"key": key, "label": LABEL[key], "old": before[key], "new": after[key]})
        if key == "onboot":
            params["onboot"] = 1 if after[key] else 0
        elif key == "startup_order":
            startup = _startup_parts(config.get("startup"))
            if after[key] is None:
                startup.pop("order", None)
            else:
                startup["order"] = str(after[key])
            if startup:
                params["startup"] = ",".join(f"{k}={v}" for k, v in startup.items())
            else:
                deletes.append("startup")
        elif key == "balloon" and after[key] == after["memory"]:
            # Minimum = RAM ist Proxmox' Standard -- Schluessel entfernen statt doppeln.
            deletes.append("balloon")
        else:
            params[key] = after[key]
    if not diff:
        raise ConfigEditError("Keine Änderung -- die Werte sind bereits so gesetzt.")
    if deletes:
        params["delete"] = ",".join(deletes)
    if config.get("digest"):
        params["digest"] = config["digest"]
    return params, diff


def format_value(key: str, value: Any) -> str:
    if value is None:
        return "keine"
    if key == "onboot":
        return "an" if value else "aus"
    if key in _MB_FIELDS:
        return f"{value} MB"
    return str(value)


def describe(diff: list[dict[str, Any]], pending: list[str]) -> str:
    changed = ", ".join(
        f"{d['label']} {format_value(d['key'], d['old'])} → {format_value(d['key'], d['new'])}" for d in diff
    )
    text = f"Geändert: {changed}."
    labels = [LABEL.get(k, k) for k in pending]
    if labels:
        text += f" Wirksam erst nach einem Neustart des Gasts: {', '.join(labels)}."
    return text


def pending_keys(rows: list[dict[str, Any]]) -> list[str]:
    """Aus GET .../pending: welche unserer Felder noch auf einen Neustart warten.
    `startup` zaehlt als `startup_order`."""
    keys: list[str] = []
    for row in rows:
        key = row.get("key")
        if "pending" not in row and not row.get("delete"):
            continue
        mapped = "startup_order" if key == "startup" else key
        if mapped in LABEL and mapped not in keys:
            keys.append(mapped)
    return keys
