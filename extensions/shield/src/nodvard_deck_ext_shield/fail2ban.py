"""Fail2ban-Einstellungen je Server, ausschließlich über das Hilfsskript."""

from __future__ import annotations

import configparser
import ipaddress
import re
import shlex
from typing import Any

from . import antivirus as av
from . import intrusion as ix

HELPER_VERSION = 19
# Untergrenzen, damit eine Freigabe Fail2ban nicht praktisch abschalten kann (ignoreip = 0.0.0.0/0, bantime = 1).
MIN_SECONDS = 60
MIN_PREFIX_V4 = 8
MIN_PREFIX_V6 = 32
MAX_RETRY = 20
# Nicht öffentliche Bereiche (übliche Ausnahmen fürs eigene Netz) zählen nicht in die Summe. Bewusst eine feste Liste:
# `is_private` hielte auch 0.0.0.0/0 für privat.
NON_PUBLIC = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10",
    "::1/128", "fc00::/7", "fe80::/10"))
_IGNORE_ENTRY = re.compile(r"[0-9a-fA-F:./]+")
FIELDS = ("bantime", "findtime", "maxretry", "ignoreip", "jails", "deck_address", "available_jails", "sshd_backend")


def address(raw: str) -> str:
    return ix.canonical_ip(raw.strip().split("%", 1)[0])


def _check_ignored(ignored: list[str], peer: str) -> None:
    """Alle Ausnahmen zusammen dürfen höchstens ein IPv4-/8 und ein IPv6-/32 abdecken (auch als abgebildete Adressen)."""
    v4: list[Any] = []
    v6: list[Any] = []
    for entry in ignored:
        if entry == peer:  # die Deck-Adresse selbst gehört immer dazu
            continue
        network = ipaddress.ip_network(entry, strict=False)
        if network.version == 6:
            for special in ("::ffff:0:0/96", "::/96"):  # abgebildet und kompatibel: wie die IPv4-Adresse zählen
                block = ipaddress.ip_network(special)
                if network.overlaps(block):
                    if network.prefixlen < 96:
                        raise ValueError(f"Die Ausnahme {entry} deckt IPv4-Adressen in IPv6-Schreibweise ab.")
                    network = ipaddress.ip_network((int(network.network_address) & 0xFFFFFFFF, network.prefixlen - 96))
                    break
        if any(network.version == p.version and network.subnet_of(p) for p in NON_PUBLIC):
            continue  # Heimnetz, Loopback, Link-Local, CGNAT: keine öffentlichen Adressen, zählen nicht
        (v4 if network.version == 4 else v6).append(network)
    for nets, prefix, name in ((v4, MIN_PREFIX_V4, "IPv4"), (v6, MIN_PREFIX_V6, "IPv6")):
        total = sum(n.num_addresses for n in ipaddress.collapse_addresses(nets))
        if total > 2 ** ((32 if name == "IPv4" else 128) - prefix):
            raise ValueError(f"Die Ausnahmen decken bei {name} zusammen zu viele Adressen ab (höchstens ein /{prefix}), "
                             "sonst bliebe Fail2ban praktisch wirkungslos.")


def settings(values: dict[str, Any], strict: bool = False) -> dict[str, Any]:
    """Prüft die Werte. `strict` (beim Schreiben) verlangt zusätzlich die Untergrenzen; beim Lesen zeigen wir auch schwache Werte an."""
    result: dict[str, Any] = {}
    for key, maximum in (("bantime", 31536000), ("findtime", 31536000), ("maxretry", 1000)):
        value = values.get(key)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"Ungültiger Wert für {key} (1 bis {maximum}).")
        if strict and key in ("bantime", "findtime") and value < MIN_SECONDS:
            raise ValueError(f"{key} muss mindestens {MIN_SECONDS} Sekunden betragen, sonst bremst Fail2ban nicht.")
        if strict and key == "maxretry" and value > MAX_RETRY:
            raise ValueError(f"maxretry darf höchstens {MAX_RETRY} sein, sonst bremst Fail2ban nicht.")
        result[key] = value
    for key in ("ignoreip", "jails", "available_jails"):
        value = values.get(key)
        # Eine schon geprüfte Liste enthält zusätzlich die Deck-Adresse.
        limit = 257 if key == "ignoreip" else 256
        if not isinstance(value, list) or len(value) > limit or not all(isinstance(v, str) for v in value):
            raise ValueError(f"Ungültige Liste für {key}.")
    available = sorted(set(values["available_jails"]) | {"sshd"})
    if any(not ix.valid_jail(j) or j.upper() in {"DEFAULT", "INCLUDES"} for j in available):
        raise ValueError("Ungültiger Jail-Name.")
    jails = sorted(set(values["jails"]))
    if "sshd" not in jails or not set(jails) <= set(available):
        raise ValueError("sshd ist Pflicht; weitere Jails müssen auf dem Server erkannt worden sein.")
    # Nur Zeichen einer Adresse bzw. eines Netzes: sonst käme z. B. über eine Zonenangabe (`%…`) ein Zeilenumbruch in die Datei.
    if any(not isinstance(v, str) or _IGNORE_ENTRY.fullmatch(v) is None for v in values["ignoreip"]):
        raise ValueError("Ungültige Ausnahme oder SSH-Quelladresse.")
    try:
        peer = address(values["deck_address"])
        ignored = [str(ipaddress.ip_network(v, strict=False)) if "/" in v else ix.canonical_ip(v) for v in values["ignoreip"]]
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError("Ungültige Ausnahme oder SSH-Quelladresse.") from exc
    if strict:
        _check_ignored(ignored, peer)
    result.update(ignoreip=sorted(set(ignored) | {peer}), jails=jails, available_jails=available, deck_address=peer)
    backend = values.get("sshd_backend")
    if backend not in (None, "systemd"):
        raise ValueError("Ungültiges SSH-Backend.")
    result["sshd_backend"] = backend
    return result


def content(values: dict[str, Any]) -> str:
    s = settings(values, strict=True)
    lines = []
    for jail in s["available_jails"]:
        lines += [f"[{jail}]", f"enabled = {'true' if jail in s['jails'] else 'false'}"]
        if jail in s["jails"]:
            if jail == "sshd" and s["sshd_backend"]:
                lines += ["backend = systemd"]
            lines += [f"{key} = {s[key]}" for key in ("bantime", "findtime", "maxretry")]
            lines += ["ignoreip = " + " ".join(s["ignoreip"])]
        lines.append("")
    return "\n".join(lines)


def command(values: dict[str, Any]) -> str:
    s = settings(values)
    body = content(s)
    if len(body) > 65536:
        raise ValueError("Die Fail2ban-Konfiguration ist zu groß. Bitte weniger Ausnahmen oder Jails auswählen.")
    peer = s["deck_address"]
    addr = ipaddress.ip_address(peer)
    forms = {peer, addr.exploded}
    if addr.version == 4:
        mapped = ipaddress.IPv6Address(f"::ffff:{peer}")
        forms |= {f"::ffff:{peer}", mapped.compressed, mapped.exploded}
    patterns = "|".join(pattern for form in sorted(forms)
                        for pattern in ([shlex.quote(form), shlex.quote(form) + "%*"] if addr.is_link_local else [shlex.quote(form)]))
    guard = (f'peer="${{SSH_CLIENT:-${{SSH_CONNECTION:-}}}}"; case "${{peer%% *}}" in {patterns}) ;; '
             '*) echo "SSH-Quelladresse geändert oder unbekannt. Bitte Einstellungen neu laden." >&2; exit 5;; esac; ')
    return guard + av.via_helper(["fail2ban-config-write", peer, body], None,
                                 version=HELPER_VERSION, helper_only=True)


def read_command() -> str:
    return av.via_helper(["fail2ban-config-read"], None, version=HELPER_VERSION, helper_only=True)


def parse(output: str, peer: str) -> dict[str, Any]:
    parts = re.split(r"(?m)^@@([a-z]+)\n", output.replace("\r\n", "\n"))
    sections = dict(zip(parts[1::2], parts[2::2], strict=True))
    runtime = sections.get("runtime", "1").strip() == "1"
    values: dict[str, Any] = {"deck_address": peer}
    for key in ("bantime", "findtime", "maxretry"):
        values[key] = int(sections.get(key, "").strip())
    ignored = []
    for token in re.findall(r"[0-9a-fA-F:.]+(?:/[0-9]+)?", sections.get("ignoreip", "")):
        try:
            ignored.append(str(ipaddress.ip_network(token, strict=False)) if "/" in token else ix.canonical_ip(token))
        except ValueError:
            continue
    values["ignoreip"] = ignored
    values["available_jails"] = sorted({j for j in sections.get("available", "").split() if ix.valid_jail(j)})
    status = sections.get("status", "")
    match = re.search(r"Jail list:\s*([^\n]*)", status)
    values["jails"] = sorted({"sshd"} | {j.strip() for j in match[1].split(",") if ix.valid_jail(j.strip())}) if match else ["sshd"]
    managed = configparser.ConfigParser(interpolation=None)
    managed.read_string(sections.get("managed", ""))
    if managed.has_section("sshd"):
        values["sshd_backend"] = managed.get("sshd", "backend", fallback=None)
        if not runtime:
            for key in ("bantime", "findtime", "maxretry"):
                values[key] = managed.getint("sshd", key, fallback=values[key])
            values["ignoreip"] = managed.get("sshd", "ignoreip", fallback=" ".join(ignored)).split()
        if not match:
            values["jails"] = [j for j in managed.sections() if managed.getboolean(j, "enabled", fallback=False)]
    values["available_jails"] = sorted(set(values["available_jails"]) | set(values["jails"]))
    return {**settings(values), "runtime": runtime}


async def read(ctx: Any, host: Any) -> dict[str, Any]:
    with ctx.exec.read_access(host):
        seen = await ctx.exec.run(host, ix.DECK_ADDRESS_COMMAND, timeout_s=15)
    if seen.exit_code != 0 or not (seen.stdout or "").strip():
        raise ValueError(ix.DECK_ADDRESS_UNKNOWN)
    peer = address(seen.stdout)
    with ctx.exec.read_access(host):
        result = await ctx.exec.run(host, read_command(), timeout_s=30)
    if result.exit_code != 0:
        output = av.helper_text((result.stdout or "") + (result.stderr or ""))
        raise ValueError(av.no_root_message(output) if av.NO_ROOT in output else output or "Fail2ban ist nicht lesbar.")
    try:
        return parse(result.stdout or "", peer)
    except (ValueError, configparser.Error) as exc:
        raise ValueError("Die Fail2ban-Einstellungen sind nicht vollständig lesbar.") from exc
