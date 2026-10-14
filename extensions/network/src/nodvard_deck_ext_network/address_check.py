"""Adress-Check: ordnet das Ziel jedes Proxy-Hosts einem Docker-Container zu und meldet, ob der läuft.

Die Container kommen nur lesend aus der `ServiceCatalog`-Fähigkeit der Service-Matrix; ohne sie gibt es keinen Check.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

NPM_IMAGE_HINT = "nginx-proxy-manager"
# Ziel "dieser Rechner": im Host-Netz der Server des Proxy Managers, im Bridge-Netz der Proxy Manager selbst.
LOOPBACK_TARGETS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})  # nosec B104 - Vergleichswert, kein Lauschen
HOST_TARGETS = frozenset({"host.docker.internal"})
# Gateways der Docker-Netze (`docker0` 172.17.0.1, Compose ab 172.18.0.1); nicht 172.16.0.1, das ist oft ein Heimnetz-Router.
_DOCKER_GATEWAY_RE = re.compile(r"^172\.(?:1[7-9]|2\d|3[01])\.0\.1$")

RUNNING = "running"
STOPPED_STATES = frozenset({"exited", "created", "dead"})
# `docker start` lehnt einen Container im Zustand `dead` ab, dafür gibt es keinen Start-Knopf.
STARTABLE_STATES = frozenset({"exited", "created"})
# Domains im Heimnetz: `web.fritz.box` ist der Server `web`, `web.example.com` nicht.
LOCAL_SUFFIXES = ("fritz.box", "lan", "local", "home.arpa", "internal", "localdomain", "home", "intranet")
STATE_TEXT = {
    "running": ("läuft", "good"),
    "restarting": ("startet ständig neu", "warn"),
    "paused": ("ist angehalten", "warn"),
    "removing": ("wird gerade entfernt", "warn"),
}
MAX_NAMES_IN_TEXT = 3


@dataclass
class DockerHost:
    key: str
    name: str
    address: str
    reachable: bool = True
    containers: list[dict[str, Any]] = field(default_factory=list)


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().rstrip(".")
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        return str(ipaddress.ip_address(text))  # IPv6 in einer Schreibweise (`::1` statt `0:0:0:0:0:0:0:1`)
    except ValueError:
        return text


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _host_key(row: dict[str, Any]) -> str:
    if row.get("host_id"):
        return str(row["host_id"])
    # Platzhalter eines nicht erreichbaren Servers tragen nur `id` = "<server-id>:__error__".
    return str(row.get("id") or "").rsplit(":", 1)[0] or str(row.get("host") or "")


def docker_hosts(rows: list[dict[str, Any]]) -> dict[str, DockerHost]:
    """Die Zeilen aus `ServiceCatalog.list_services()` je Server gruppiert."""
    hosts: dict[str, DockerHost] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _host_key(row)
        if not key:
            continue
        entry = hosts.get(key)
        if entry is None:
            entry = hosts[key] = DockerHost(key=key, name=str(row.get("host") or key), address=_norm(row.get("host_address")))
        if not entry.address and row.get("host_address"):
            entry.address = _norm(row.get("host_address"))
        if row.get("unreachable") is True:
            entry.reachable = False
            continue
        if row.get("container") or row.get("name"):
            entry.containers.append(row)
    return hosts


def _container_name(row: dict[str, Any]) -> str:
    return str(row.get("container") or row.get("name") or "")


def _is_local_suffix(rest: str, extra: frozenset[str]) -> bool:
    return rest in extra or any(rest == s or rest.endswith(f".{s}") for s in LOCAL_SUFFIXES)


def _matches_address(target: str, host: DockerHost, suffixes: frozenset[str] = frozenset()) -> bool:
    """Ziel = Adresse oder Name des Servers. Ein Name mit Domain passt nur bei gleicher, lokaler (`LOCAL_SUFFIXES`)
    oder der Domain des Proxy Managers (`suffixes`); nie über eine IP-Adresse."""
    if not target:
        return False
    name = _norm(host.name)
    if target in (host.address, name):
        return True
    if _is_ip(target):
        return False
    first, _, rest = target.partition(".")
    for candidate in {name, host.address} - {""}:
        if _is_ip(candidate):
            continue
        c_first, _, c_rest = candidate.partition(".")
        if first != c_first:
            continue
        if rest and rest != c_rest and not _is_local_suffix(rest, suffixes):
            continue
        return True
    return False


def _domain_of(url: str | None) -> frozenset[str]:
    """Die Domain des Proxy Managers (`npm.home.example` -> `home.example`): Namen darin gelten als lokal."""
    host = _norm(urlsplit(url).hostname) if url else ""
    if not host or _is_ip(host) or "." not in host:
        return frozenset()
    return frozenset({host.partition(".")[2]})


def _npm_containers(host: DockerHost) -> list[dict[str, Any]]:
    return [c for c in host.containers if NPM_IMAGE_HINT in str(c.get("image") or "").lower()]


def npm_docker_host(hosts: dict[str, DockerHost], npm_url: str | None) -> DockerHost | None:
    """Der Server des Nginx Proxy Managers: am Image erkannt (laufender vor gestopptem), sonst an der Adresse aus den
    Einstellungen. Mehrdeutig: keiner."""
    for running_only in (True, False):
        by_image = [
            h for h in hosts.values()
            if any(not running_only or str(c.get("state") or "").lower() == RUNNING for c in _npm_containers(h))
        ]
        if len(by_image) == 1:
            return by_image[0]
        if by_image:
            break
    if npm_url:
        address = _norm(urlsplit(npm_url).hostname)
        by_address = [h for h in hosts.values() if _matches_address(address, h, _domain_of(npm_url))]
        if len(by_address) == 1:
            return by_address[0]
    return None


def _service_match(container: dict[str, Any], target: str) -> bool:
    """Gleicher Containername, oder genau der Compose-Dienst `target` im Projekt des Containers."""
    name = _norm(_container_name(container))
    if name == target:
        return True
    project = _norm(container.get("compose_project"))
    if not project:
        return False
    for sep in ("-", "_"):
        head = f"{project}{sep}{target}{sep}"
        if name.startswith(head) and name[len(head):].isdigit():
            return True
    return False


def _names(found: list[tuple[DockerHost, dict[str, Any]]]) -> str:
    parts = [f"{_container_name(c)} auf {h.name}" for h, c in found[:MAX_NAMES_IN_TEXT]]
    more = len(found) - MAX_NAMES_IN_TEXT
    return ", ".join(parts) + (f" und {more} weitere" if more > 0 else "")


def _result(status: str, tone: str, text: str, *, host: DockerHost | None = None, container: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "tone": tone,
        "text": text,
        "container": _container_name(container) if container else None,
        "docker_host": host.name if host else None,
        "docker_host_id": host.key if host and container else None,
        "can_start": bool(container) and str(container.get("state") or "").lower() in STARTABLE_STATES,
    }


def _container_result(host: DockerHost, container: dict[str, Any], *, sure: bool = True) -> dict[str, Any]:
    """`sure=False`: nur vermutlich dieser Container (Name gefunden, aber der Server des Proxy Managers ist unbekannt).
    Dann nie rot und kein Start-Knopf."""
    name = _container_name(container)
    state = str(container.get("state") or "").lower()
    lead = f"Führt {'' if sure else 'vermutlich '}zum Container {name} auf {host.name}"
    if state == RUNNING:
        result = _result("running", "good", f"{lead} – läuft.", host=host, container=container)
    elif state in STOPPED_STATES:
        result = _result("stopped", "bad", f"{lead} – der ist gestoppt. Die Seite ist so vermutlich nicht erreichbar.", host=host, container=container)
    else:
        text, tone = STATE_TEXT.get(state, (f"Zustand „{state or 'unbekannt'}“", "warn"))
        result = _result(state or "unknown", tone, f"{lead} – {text}.", host=host, container=container)
    if not sure:
        result["can_start"] = False
        if result["tone"] in ("bad", "warn"):
            result["tone"] = "neutral"
    return result


def check_proxy_host(
    proxy: dict[str, Any], hosts: dict[str, DockerHost], npm_host: DockerHost | None, suffixes: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    # Das Ziel ist ein Docker-Server, ein Name für den Server des Proxy Managers (localhost, Gateway) oder ein Containername.
    # Nicht geprüft: Container im Host-Netz (zeigen keine Ports), Netzwerk-Aliase und weitere Orte (`locations`) eines
    # Proxy-Hosts. An 127.0.0.1 gebundene Ports zählen nicht, weil sie von außen unerreichbar sind.
    target = _norm(proxy.get("forward_host"))
    port = proxy.get("forward_port")
    port = port if isinstance(port, int) and 0 < port <= 65535 else None
    if not target:
        return _result("unknown", "neutral", "Kein Ziel eingetragen.")

    server: DockerHost | None = None
    by_address = [h for h in hosts.values() if _matches_address(target, h, suffixes)]
    if len(by_address) == 1:
        server = by_address[0]  # die Adresse eines Servers geht vor (auch wenn sie wie ein Docker-Gateway aussieht)
    elif target in LOOPBACK_TARGETS or target in HOST_TARGETS or _DOCKER_GATEWAY_RE.match(target):
        if npm_host is None:
            return _result(
                "unknown", "neutral",
                "Das Ziel ist der Server, auf dem der Nginx Proxy Manager läuft. Welcher das ist, ließ sich nicht erkennen.",
            )
        if target in LOOPBACK_TARGETS:
            npm = _npm_containers(npm_host)
            # Nur im Host-Netz ist `localhost` der Server. Im Bridge-Netz (der Container veröffentlicht Ports) oder bei
            # nicht erkennbarem Container wäre jede Zuordnung geraten.
            if len(npm) != 1 or npm[0].get("published_ports"):
                return _result(
                    "unknown", "neutral",
                    f"{target} ist hier vermutlich der Nginx Proxy Manager selbst (er läuft im Docker-Netz), nicht der "
                    f"Server {npm_host.name}. Für einen Dienst auf dem Server passt meist dessen Adresse oder "
                    "host.docker.internal.",
                )
        server = npm_host

    if server is not None:
        if not server.reachable:
            return _result("unreachable", "warn", f"{server.name} ist gerade nicht erreichbar, das Ziel ließ sich nicht prüfen.", host=server)
        if port is None:
            return _result("unknown", "neutral", f"Das Ziel ist {server.name}, aber ohne Port lässt sich kein Container zuordnen.", host=server)
        found = [(server, c) for c in server.containers if port in (c.get("published_ports") or [])]
        running = [(h, c) for h, c in found if str(c.get("state") or "").lower() == RUNNING]
        if len(running) == 1:
            found = running  # alte gestoppte Container mit derselben Port-Zuordnung sind häufig und zählen nicht mit
        if len(found) == 1:
            return _container_result(*found[0])
        if len(found) > 1:
            return _result("ambiguous", "neutral", f"Mehrere Container belegen Port {port}: {_names(found)}. Nicht eindeutig.", host=server)
        return _result(
            "unknown", "neutral",
            f"Auf {server.name} belegt kein Container den Port {port}. Vielleicht ein Dienst außerhalb von Docker.",
            host=server,
        )

    if _is_ip(target):
        return _result("unknown", "neutral", "Das Ziel ist kein bekannter Docker-Server. Vielleicht ein Dienst außerhalb von Docker.")

    # Docker löst Namen nur im eigenen Netz auf: ist der Server des Proxy Managers bekannt, nur dort suchen.
    searched = [npm_host] if npm_host is not None else list(hosts.values())
    found = [(h, c) for h in searched for c in h.containers if _service_match(c, target)]
    if len(found) == 1:
        return _container_result(*found[0], sure=npm_host is not None)
    if len(found) > 1:
        return _result("ambiguous", "neutral", f"Mehrere Container passen zum Namen {target}: {_names(found)}. Nicht eindeutig.")
    if npm_host is not None:
        if not npm_host.reachable:
            return _result("unreachable", "warn", f"{npm_host.name} ist gerade nicht erreichbar, das Ziel ließ sich nicht prüfen.", host=npm_host)
        return _result(
            "unknown", "neutral",
            f"Auf {npm_host.name}, wo der Nginx Proxy Manager läuft, heißt kein Container {target}. "
            "Vielleicht ein Dienst außerhalb von Docker.",
        )
    unreachable = [h.name for h in hosts.values() if not h.reachable]
    if unreachable:
        return _result(
            "unreachable", "warn",
            f"Kein Container heißt {target}. Nicht erreichbar und deshalb nicht geprüft: {', '.join(unreachable)}.",
        )
    return _result("unknown", "neutral", f"Kein Container heißt {target}. Vielleicht ein Dienst außerhalb von Docker.")


def check_all(proxies: list[dict[str, Any]], rows: list[dict[str, Any]], *, npm_url: str | None) -> list[dict[str, Any]]:
    """Je Proxy-Host ein Ergebnis (`proxy_id` und Felder aus `_result`); ausgeschaltete Hosts werden nie rot."""
    hosts = docker_hosts(rows)
    npm_host = npm_docker_host(hosts, npm_url)
    suffixes = _domain_of(npm_url)
    out: list[dict[str, Any]] = []
    for proxy in proxies:
        result = check_proxy_host(proxy, hosts, npm_host, suffixes)
        if not proxy.get("enabled"):
            # Ausgeschaltet: dass dahinter nichts läuft, ist meist gewollt (nicht rot, kein Start-Knopf).
            result["can_start"] = False
            if result["tone"] in ("bad", "warn"):
                result["tone"] = "neutral"
        out.append({"proxy_id": proxy.get("id"), **result})
    return out


def problem_count(checks: list[dict[str, Any]]) -> int:
    return sum(1 for c in checks if c.get("tone") == "bad")
