"""Dienstkatalog (`ServiceCatalog`) und Container-Aktionen des Docker-Moduls, per `docker ps` über SSH (`ctx.exec.run()`).
Jeder Container eines getaggten Hosts erscheint von selbst und verschwindet wieder, sobald er entfernt wird."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import Risk

from . import commands
from .commands import COMPOSE_PROJECT_RE, CONTAINER_NAME_RE, DOCKER_ID_RE
from .compose_errors import scrub_compose_output
from .locks import StackLocks, busy_text

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext


def _json_pattern(regex: re.Pattern[str]) -> str:
    """Für das JSON-Schema (ECMA-Syntax kennt `\\Z` nicht): `\\Z` -> `$`."""
    return regex.pattern.replace("\\Z", "$")


COMPOSE_DIRS_TTL_S = 600.0
"""So lange gilt der gemerkte Stand der Projektordner eines Hosts, bevor die Orte ihn neu lesen."""
MAX_COMPOSE_DIRS = 20
_PORTAINER_DIR_PREFIX = "/data/compose/"
"""Portainer legt seine Stacks in seinem Container unter `/data/compose/<n>` ab; diesen Ordner gibt es auf dem Host nicht."""


def parse_compose_dirs(text: str) -> dict[str, str]:
    """Compose-Projekt -> Projektordner aus der Ausgabe von `commands.ps_all()` (der Ordner steht im
    achten Feld). Nur Ordner auf dem Host: absolut, ohne Steuerzeichen und `..`, nicht von Portainer; je Projekt gilt
    der erste."""
    dirs: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split("|", 7)
        if len(parts) < 8:
            continue
        project, folder = parts[5].strip(), parts[7].strip()
        if not project or project in dirs or not folder.startswith("/") or folder.startswith(_PORTAINER_DIR_PREFIX):
            continue
        if any(ord(ch) < 32 for ch in folder) or ".." in folder.split("/"):
            continue
        dirs[project] = folder if folder == "/" else folder.rstrip("/")
    return dirs


_SIZE_RE = re.compile(r"^\s*([\d.]+)\s*([KMGTP]?i?B)\s*$", re.IGNORECASE)
_SIZE_FACTOR = {
    "B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12,
    "KIB": 2**10, "MIB": 2**20, "GIB": 2**30, "TIB": 2**40,
}


def parse_size(text: str) -> int | None:
    """"45.3MiB" -> Byte; Dockers eigene Schreibweise (binär und dezimal)."""
    match = _SIZE_RE.match(text or "")
    if match is None:
        return None
    factor = _SIZE_FACTOR.get(match.group(2).upper())
    return int(float(match.group(1)) * factor) if factor else None


def parse_stats_line(line: str) -> tuple[str, dict[str, Any]] | None:
    """Eine Zeile von `docker stats --no-stream` -> (Name, Werte); `None` bei einer unlesbaren Zeile."""
    parts = line.split("|")
    if len(parts) < 4 or not parts[0].strip():
        return None
    try:
        cpu: float | None = float(parts[1].strip().rstrip("%"))
    except ValueError:
        cpu = None
    used_text, _, limit_text = parts[2].partition("/")
    mem_used, mem_limit = parse_size(used_text), parse_size(limit_text)
    # "0B / 0B": der Host meldet keinen Speicher (auf dem Raspberry Pi ist die Speicher-Abrechnung der cgroups im Kernel
    # standardmäßig aus). Dann ist der RAM unbekannt, nicht 0.
    if not mem_limit:
        mem_used = mem_limit = None
    return parts[0].strip(), {"cpu_percent": cpu, "mem_used": mem_used, "mem_limit": mem_limit}


ENVIRONMENT_TTL_S = 600.0
"""So lange gilt, was `docker info` über CPU und Arbeitsspeicher eines Hosts gesagt hat."""
_LXC = frozenset({"lxc", "lxc-libvirt"})
_CONTAINERS = frozenset({"openvz", "systemd-nspawn", "docker", "podman", "rkt", "wsl", "proot", "pouch"})
_VIRT_WORD_RE = re.compile(r"^[a-z0-9-]{1,32}\Z")
_VERSION_RE = re.compile(r"^[0-9A-Za-z.+~-]{1,40}\Z")


def parse_environment(info: str, virt: str | None) -> dict[str, Any] | None:
    """CPU-Zahl, Arbeitsspeicher und Version aus `docker info` und die Umgebung (`lxc`, `container`, `vm`, `none`, unbekannt
    `None`); `None` insgesamt, wenn der Docker-Dienst nicht antwortet und `docker info` deshalb 0 CPUs meldet."""
    try:
        raw = json.loads(info or "")
    except ValueError:
        return None
    cpus, memory = (raw.get("NCPU"), raw.get("MemTotal")) if isinstance(raw, dict) else (None, None)
    if not isinstance(cpus, int) or isinstance(cpus, bool) or not 0 < cpus <= 4096:
        return None
    version = raw.get("ServerVersion")
    word = (virt or "").strip()
    if not _VIRT_WORD_RE.match(word):
        kind = None
    elif word == "none":
        kind = "none"
    elif word in _LXC:
        kind = "lxc"
    elif word in _CONTAINERS:
        kind = "container"
    else:
        kind = "vm"
    return {
        "virtualization": kind,
        "cpus": cpus,
        "mem_total": memory if isinstance(memory, int) and not isinstance(memory, bool) and memory > 0 else None,
        "docker_version": version if isinstance(version, str) and _VERSION_RE.match(version) else None,
    }


def parse_restart_counts(text: str) -> dict[str, int]:
    """Zeilen `/<name>|<Anzahl>` aus `commands.restart_counts()` -> Name -> Anzahl; nur Container, die neu gestartet wurden."""
    counts: dict[str, int] = {}
    for line in (text or "").splitlines():
        name, sep, count = line.strip().partition("|")
        name = name.removeprefix("/")
        if sep and CONTAINER_NAME_RE.match(name) and re.fullmatch(r"[0-9]{1,9}", count) and int(count) > 0:
            counts[name] = int(count)
    return counts


def _own_container_id() -> str:
    return socket.gethostname()


def is_own_container(container_id: str) -> bool:
    """Läuft dieser Container Nodvard Deck selbst? Vergleich auf die kurze ID (12 Zeichen, Hostname-Vorgabe von Docker);
    "Stoppen" darauf würde die Oberfläche abschalten, aus der man ihn wieder starten müsste."""
    own = _own_container_id()
    if not container_id or len(own) < 12:
        return False
    return container_id.startswith(own) or own.startswith(container_id)

# Nur IPv4-Host-Bindungen (`0.0.0.0:8080->80/tcp`): `docker ps` listet denselben Port oft zusätzlich als IPv6-Zwilling
# (`:::8080->80/tcp`). Gleich nummerierte Bindungen fasst Docker zu Bereichen zusammen (`0.0.0.0:80-81->80-81/tcp`),
# Bereiche gleicher Länge werden Port für Port aufgelöst.
_PORT_RE = re.compile(
    r"(?:^|[\s,])(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})(?:-(\d{1,5}))?->(\d{1,5})(?:-(\d{1,5}))?/tcp"
)
MAX_RANGE_PORTS = 256
"""Höchstens so viele Ports je Bereich (wie bei `published_ports`)."""

WEB_PORTS: tuple[int, ...] = (
    80, 8080, 443, 8443, 9443, 9000, 3000, 3001, 5000, 8096, 8920, 32400, 8123, 1880, 8384, 8989, 7878, 9696, 8686,
    6767, 5055, 8081, 8088, 8090, 8888, 9090, 2283, 5678, 7575, 61208, 19999, 81,
)
"""Ports im Container, hinter denen üblicherweise eine Weboberfläche steckt, in dieser Reihenfolge bevorzugt
(80 vor 443: ein eigenes Zertifikat würde im Browser erst eine Warnung zeigen)."""
NON_WEB_PORTS = frozenset({
    20, 21, 22, 23, 25, 53, 67, 68, 110, 111, 123, 137, 138, 139, 143, 161, 389, 445, 465, 554, 587, 636, 993, 995,
    1194, 1883, 1935, 2049, 2181, 3306, 3478, 4369, 5349, 5353, 5432, 5672, 5900, 6379, 6881, 8009, 8333, 8545, 8546,
    8554, 8555, 8883, 9092, 11211, 25565, 27015, 27017, 51413, 51820,
})
"""Ports im Container, die nie eine Weboberfläche sind (DNS, SSH, Mail, Datenbanken, MQTT, VPN, Torrent, Kamera-
Streams per RTSP/RTMP, VNC, AJP, Blockchain-Knoten ...)."""
TLS_PORTS = frozenset({443, 8443, 9443})
"""Hinter diesen Ports im Container spricht die App https (Ausnahmen: `_ImageRule.scheme`)."""
PLAIN_HTTP_PORTS = frozenset({80})
"""Hier spricht die App immer http, auch wenn als Link-Schema https eingestellt ist."""
FALLBACK_WEB_RANGE = range(8000, 9000)
"""Kein bekannter Web-Port: ein Port im Container aus diesem Bereich ist fast immer eine Weboberfläche."""


@dataclass(frozen=True)
class _ImageRule:
    prefer: tuple[int, ...] = ()
    """Ports im Container, die bei diesem Image die Weboberfläche sind (vor allen anderen)."""
    never: frozenset[int] = frozenset()
    """Ports im Container, die bei diesem Image keine Weboberfläche sind."""
    path: str = ""
    no_web: bool = False
    scheme: str | None = None
    """Fester Schema-Wert, wenn das Image anders spricht, als der Port vermuten lässt."""


# Was sich nicht aus den Ports allein ablesen lässt: Pi-hole hat seine Oberfläche unter `/admin/`, Portainer belegt 8000 für
# den Edge-Tunnel, Portainer-Agent und Docker-Registry haben gar keine Oberfläche, beim Nginx Proxy Manager liegt die
# Verwaltung auf 81 (80/443 sind die Weiterleitungen selbst), code-server spricht auf 8443 http.
_PORTAINER = _ImageRule(prefer=(9443, 9000), never=frozenset({8000, 9001}))
_PIHOLE = _ImageRule(prefer=(80, 443), path="/admin/")
_PLEX = _ImageRule(prefer=(32400,), path="/web")
_UNIFI = _ImageRule(prefer=(8443,), scheme="https")
IMAGE_RULES: dict[str, _ImageRule] = {
    "pihole/pihole": _PIHOLE,
    "pi-hole/pihole": _PIHOLE,
    "portainer/portainer-ce": _PORTAINER,
    "portainer/portainer-ee": _PORTAINER,
    "portainer/portainer": _PORTAINER,
    "portainer/agent": _ImageRule(no_web=True),
    "jc21/nginx-proxy-manager": _ImageRule(prefer=(81,)),
    "jlesage/nginx-proxy-manager": _ImageRule(prefer=(8181,)),
    "traefik": _ImageRule(prefer=(8080,)),
    "plexinc/pms-docker": _PLEX,
    "linuxserver/plex": _PLEX,
    "minio/minio": _ImageRule(prefer=(9001,), never=frozenset({9000})),
    "linuxserver/code-server": _ImageRule(scheme="http"),
    "linuxserver/unifi-network-application": _UNIFI,
    "jacobalberty/unifi": _UNIFI,
    "registry": _ImageRule(no_web=True),
}
"""Keine Regel greift, wenn `docker ps` statt des Namens nur die Image-ID zeigt (der Tag zeigt dann schon auf ein neueres
Image, etwa nach `docker compose pull` ohne `up`); es zählen nur die Ports."""


def image_key(image: str) -> str:
    """`lscr.io/linuxserver/plex:latest` -> `linuxserver/plex`, `pihole/pihole:2024.07` -> `pihole/pihole`,
    `nginx` -> `nginx`: ohne Registry, Tag und Digest, klein geschrieben."""
    name = (image or "").strip().lower().split("@", 1)[0]
    parts = name.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        parts = parts[1:]
    if parts and ":" in parts[-1]:
        parts[-1] = parts[-1].split(":", 1)[0]
    if len(parts) > 1 and parts[0] == "library":
        parts = parts[1:]
    return "/".join(parts)


def _port_mappings(ports: str) -> dict[int, int]:
    """Port im Container -> erster Port auf dem Server, nur IPv4 und nicht nur am Server selbst erreichbar."""
    mappings: dict[int, int] = {}
    for match in _PORT_RE.finditer(ports or ""):
        if _loopback_bind(match.group(1)):
            continue
        host_start, container_start = int(match.group(2)), int(match.group(4))
        host_end = int(match.group(3)) if match.group(3) else host_start
        container_end = int(match.group(5)) if match.group(5) else container_start
        if host_end < host_start or host_end - host_start != container_end - container_start:
            continue  # unterschiedlich lange Bereiche: welcher Port wohin gehört, steht nicht fest
        if not (0 < host_start and host_end <= 65535 and 0 < container_start and container_end <= 65535):
            continue
        for offset in range(min(host_end - host_start + 1, MAX_RANGE_PORTS)):
            mappings.setdefault(container_start + offset, host_start + offset)
    return mappings


def _guess_url(ports: str, address: str, scheme: str, image: str = "") -> str | None:
    """Adresse der Weboberfläche eines Containers, oder None. Entscheidend ist der Port im Container (rechts von `->`):
    `0.0.0.0:8088->80/tcp` ist Web auf Port 8088, `0.0.0.0:53->53/tcp` ist DNS."""
    rule = IMAGE_RULES.get(image_key(image), _ImageRule())
    if rule.no_web:
        return None
    usable = {c: h for c, h in _port_mappings(ports).items() if c not in NON_WEB_PORTS and c not in rule.never}
    chosen = next((c for c in (*rule.prefer, *WEB_PORTS) if c in usable), None)
    if chosen is None:
        chosen = min((c for c in usable if c in FALLBACK_WEB_RANGE), default=None)
    if chosen is None:
        return None
    if rule.scheme:
        url_scheme = rule.scheme
    elif chosen in TLS_PORTS:
        url_scheme = "https"
    elif chosen in PLAIN_HTTP_PORTS:
        url_scheme = "http"
    else:
        url_scheme = scheme
    return f"{url_scheme}://{address}:{usable[chosen]}{rule.path}"


MAX_URL_OVERRIDES = 500
"""Höchstens so viele eigene Kachel-Adressen (Einstellung `url_overrides`)."""
MAX_URL_LENGTH = 2000


_HOST_RE = re.compile(r"[\w.-]+")


def clean_tile_url(value: str) -> str:
    """Prüft eine eigene Kachel-Adresse wie die Apps-Seite (`services/custom_apps.clean_url` im Kern), sonst `ValueError`.
    Nur http/https mit Rechnername oder IP, ohne Benutzername und Passwort (sie stünden für jeden lesbar in der Kachel) und
    ohne Leer-, Steuer- und unsichtbare Zeichen oder Rückwärts-Schrägstrich."""
    text = (value or "").strip()
    if not text:
        raise ValueError("Bitte eine Adresse angeben.")
    if len(text) > MAX_URL_LENGTH:
        raise ValueError(f"Die Adresse ist zu lang (höchstens {MAX_URL_LENGTH} Zeichen).")
    if not text.lower().startswith(("http://", "https://")):
        raise ValueError("Bitte eine Adresse mit http:// oder https:// angeben, z. B. http://192.0.2.10:8080.")
    if "\\" in text or any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in text):
        raise ValueError("In der Adresse dürfen keine Leerzeichen, Steuerzeichen oder Rückwärts-Schrägstriche stehen.")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise ValueError("Die Adresse oder ihr Port ist ungültig.") from None
    if "@" in parts.netloc:
        raise ValueError("Die Adresse darf keinen Benutzernamen und kein Passwort enthalten.")
    host = parts.hostname
    if not host or not _valid_host(host):
        raise ValueError("Die Adresse braucht einen Rechnernamen oder eine IP-Adresse, z. B. http://192.0.2.10:8080.")
    if port == 0:
        raise ValueError("Die Adresse oder ihr Port ist ungültig.")
    return text


def _valid_host(host: str) -> bool:
    if ":" in host:  # IPv6-Adresse (die Klammern hat urlsplit schon entfernt)
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return False
        return True
    return _HOST_RE.fullmatch(host) is not None


def url_origin(url: str) -> str:
    """`https://host:port` ohne Pfad und Parameter -- das, was ins Protokoll darf."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def url_overrides(settings: dict[str, Any]) -> dict[str, str]:
    """Eigene Kachel-Adressen aus den Einstellungen: `<host_id>:<container>` -> Adresse. Kaputte Einträge fallen weg."""
    raw = settings.get("url_overrides")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        try:
            # Auch beim Lesen prüfen: kommt die Liste an der Route vorbei in die Einstellungen (Datenbank von Hand,
            # Wiederherstellung), soll trotzdem nie ein `javascript:`-Link in eine Kachel.
            out[key] = clean_tile_url(value)
        except ValueError:
            continue
    return out


_PUBLISHED_RE = re.compile(
    r"(?:^|[\s,])(\d{1,3}(?:\.\d{1,3}){3}|\[[0-9a-fA-F:]*\]|:*):(\d{1,5})(?:-(\d{1,5}))?->\d{1,5}(?:-\d{1,5})?/tcp"
)


def _loopback_bind(host_ip: str) -> bool:
    """Nur am Server selbst erreichbar (`127.0.0.1:8080->80`, `[::1]:8080->80`): kein Ziel für `server-adresse:port`."""
    ip = host_ip.strip("[]")
    return ip.startswith("127.") or ip == "::1"
MAX_PUBLISHED_PORTS = 256
"""Obergrenze je Container: ein großer Bereich (`8000-9000->...`) soll die Liste nicht aufblähen."""


STOPPED_STATES = frozenset({"exited", "created", "dead", "restarting"})
"""Zustände, in denen `docker ps` keine Ports zeigt: die Zuordnung steht dann nur in der Container-Konfiguration
(`restarting`: zwischen zwei Starts läuft der Container nicht)."""
INSPECT_TIMEOUT_S = 5
MAX_INSPECT_PER_HOST = 50


def parse_port_bindings(stdout: str) -> dict[str, list[int]]:
    """Zeilen `<volle ID>|<HostConfig.PortBindings als JSON>` (`docker inspect`) -> kurze ID (12 Zeichen) -> die TCP-Ports,
    die der Container beim Start auf dem Server belegt. Ein leerer `HostPort` (Docker wählt den Port erst beim Start)
    und eine Bindung nur an `127.0.0.1`/`::1` zählen nicht; kaputte Zeilen werden übersprungen."""
    out: dict[str, list[int]] = {}
    for line in (stdout or "").splitlines():
        container_id, sep, raw = line.strip().partition("|")
        if not sep or not DOCKER_ID_RE.match(container_id):
            continue
        try:
            bindings = json.loads(raw)
        except ValueError:
            continue
        found: set[int] = set()
        if isinstance(bindings, dict):
            for key, binds in bindings.items():
                if not isinstance(key, str) or not key.endswith("/tcp") or not isinstance(binds, list):
                    continue
                for bind in binds:
                    if not isinstance(bind, dict) or _loopback_bind(str(bind.get("HostIp") or "")):
                        continue
                    host_port = bind.get("HostPort")
                    if isinstance(host_port, str) and host_port.isdigit() and 0 < int(host_port) <= 65535:
                        found.add(int(host_port))
        out[container_id[:12]] = sorted(found)[:MAX_PUBLISHED_PORTS]
    return out


def published_ports(ports: str) -> list[int]:
    """Alle TCP-Ports, die der Container auf dem Server veröffentlicht (IPv4 und IPv6, auch Bereiche), aufsteigend.
    Nicht gezählt: intern freigegebene Ports (`80/tcp` ohne `->`) und Bindungen nur an `127.0.0.1`/`::1`. Damit ordnet
    der Adress-Check der Netzwerk-Erweiterung ein Proxy-Ziel `server:port` dem Container zu, der den Port belegt."""
    found: set[int] = set()
    for match in _PUBLISHED_RE.finditer(ports or ""):
        if _loopback_bind(match.group(1)):
            continue
        start = int(match.group(2))
        end = int(match.group(3)) if match.group(3) else start
        if not 0 < start <= end <= 65535:
            continue
        for port in range(start, min(end, start + MAX_PUBLISHED_PORTS - 1) + 1):
            found.add(port)
            if len(found) >= MAX_PUBLISHED_PORTS:
                return sorted(found)
    return sorted(found)


def _error_tile(host: Any, message: str) -> dict[str, Any]:
    """Platzhalter-Kachel für einen Server, der sich nicht lesen ließ."""
    return {
        "id": f"{host.id}:__error__",
        "name": f"⚠ {host.display_name or host.name}",
        # Die Seite gruppiert nach `host`: hier muss der echte Anzeigename stehen, die Meldung gehört nur in `status`.
        "host": host.display_name or host.name,
        "state": "error",
        "status": message,
        "tone": "danger",
        "url": None,
        # Marker für Leser (Cockpit): Platzhalter eines Servers, den wir nicht lesen konnten, kein Container.
        "unreachable": True,
        # Für den Adress-Check: ein Ziel `adresse:port` dieses Servers heißt dann "nicht erreichbar", nicht "unbekannt".
        "host_address": host.address,
    }


def _tone_for_state(state: str) -> str:
    if state == "running":
        return "good"
    if state in ("restarting", "paused"):
        return "warn"
    return "neutral"


class DockerServiceCatalog:
    """Dienstkatalog über `docker ps`; `list_services()` liefert lose Dicts, deren Felder die Templates der Kachel nutzen."""

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx
        self._compose_dirs: dict[str, tuple[float, dict[str, str]]] = {}
        """Host-ID -> (Zeitpunkt, Projekt -> Ordner): der Stand aus dem letzten `docker ps`, das `list_services()` ohnehin liest."""
        self._environments: dict[str, tuple[float, dict[str, Any]]] = {}
        """Host-ID -> (Zeitpunkt, `parse_environment`)."""
        self._stopped_ports: dict[str, dict[str, list[int]]] = {}
        """Host-ID -> kurze Container-ID -> Ports gestoppter Container. Ein gestoppter Container ändert seine Zuordnung nicht
        (ein neu angelegter bekommt eine neue ID), deshalb fragt `list_services()` je Container nur einmal nach."""

    async def compose_dirs(self, host: Any) -> dict[str, str]:
        """Projektordner der Compose-Projekte eines Hosts, aus dem Stand vom letzten Lesen in `list_services()`.
        Ist er älter als `COMPOSE_DIRS_TTL_S` oder fehlt er, liest dieser Aufruf `docker ps -a` einmal für diesen Host;
        ein ausgeschalteter oder stummer Host zeigt den alten Stand (oder nichts)."""
        remembered = self._compose_dirs.get(host.id)
        if remembered is not None and time.monotonic() - remembered[0] < COMPOSE_DIRS_TTL_S:
            return remembered[1]
        if getattr(getattr(host, "status", None), "value", None) == "down":
            return remembered[1] if remembered else {}
        try:
            result = await self._ctx.exec.run(host, commands.ps_all(), timeout_s=10)
            if result.exit_code != 0:
                raise RuntimeError(result.stderr.strip() or f"Exit-Code {result.exit_code}")
        except Exception:  # noqa: BLE001 - ein alter Stand ist besser als keiner
            if remembered is not None:
                return remembered[1]
            raise
        return self._remember_compose_dirs(host.id, result.stdout)

    def _remember_compose_dirs(self, host_id: str, stdout: str) -> dict[str, str]:
        dirs = parse_compose_dirs(stdout)
        self._compose_dirs[host_id] = (time.monotonic(), dirs)
        return dirs

    async def list_services(self) -> list[dict[str, Any]]:
        """Alle Container der Docker-Hosts als lose Dicts; ein Host ohne laufenden Docker-Daemon oder ganz unerreichbar
        erscheint als `tone="danger"`-Kachel statt zu fehlen."""
        from .operate import docker_hosts  # spät: operate braucht commands, das hier schon geladen ist

        settings = await self._ctx.settings.get()
        scheme = settings.get("url_scheme") or "http"
        overrides = url_overrides(settings)

        services: list[dict[str, Any]] = []
        for host, operate in await docker_hosts(self._ctx):
            try:
                result = await self._ctx.exec.run(host, commands.ps_all(), timeout_s=10)
            except Exception as exc:  # noqa: BLE001 - ein nicht erreichbarer Host darf die anderen nicht verstecken
                services.append(_error_tile(host, f"Nicht erreichbar: {exc}"))
                continue
            if result.exit_code != 0:
                detail = result.stderr.strip() or f"Exit-Code {result.exit_code}"
                services.append(_error_tile(host, f"docker ps fehlgeschlagen: {detail}"))
                continue

            self._remember_compose_dirs(host.id, result.stdout)
            stopped: list[tuple[dict[str, Any], str]] = []
            for line in result.stdout.splitlines():
                # ID|Name|Zustand|Status|Image|Projekt|Ports|Ordner; ältere Ausgaben ohne Projekt (6 Felder) oder Ordner
                # werden weiter gelesen.
                parts = line.split("|", 7)
                if len(parts) < 5:
                    continue
                container_id = parts[0].strip()
                name = parts[1].strip()
                state = parts[2].strip().lower()
                status_str = parts[3].strip()
                image = parts[4].strip()
                project = parts[5].strip() if len(parts) > 6 else ""
                ports = (parts[6] if len(parts) > 6 else parts[5] if len(parts) > 5 else "").strip()
                if not name:
                    continue
                detected = _guess_url(ports, host.address, scheme, image)
                custom = overrides.get(f"{host.id}:{name}")
                row = {
                    "id": f"{host.id}:{name}",
                    "name": name,
                    "host": host.display_name or host.name,
                    # Was die Seite für Aktionen (POST /hosts/{host_id}/actions/container.*) und Logs braucht.
                    "host_id": host.id,
                    "container": name,
                    "is_self": is_own_container(container_id),
                    "image": image,
                    "compose_project": project or None,
                    "state": state,
                    "status": status_str,
                    "tone": _tone_for_state(state),
                    "url": custom or detected,
                    # Was Nodvard Deck selbst erkannt hat, und ob der Nutzer die Adresse überschrieben hat.
                    "url_detected": detected,
                    "url_custom": custom is not None,
                    # Für den Adress-Check (Netzwerk-Erweiterung): Proxy-Ziel `adresse:port` -> Container.
                    "host_address": host.address,
                    "published_ports": published_ports(ports),
                    # Modus „Nur bedienen“: nur Ansehen, Logs, Starten, Stoppen und Neustarten.
                    "operate_only": operate,
                }
                services.append(row)
                if state in STOPPED_STATES and DOCKER_ID_RE.match(container_id):
                    stopped.append((row, container_id[:12]))
            await self._fill_stopped_ports(host, stopped)
        return services

    async def _fill_stopped_ports(self, host: Any, stopped: list[tuple[dict[str, Any], str]]) -> None:
        """Gestoppte Container zeigen in `docker ps` keine Ports; die Zuordnung kommt aus `docker inspect`.
        Je Container wird nur beim ersten Mal gelesen. Scheitert das, bleibt `published_ports` leer und der nächste
        Aufruf versucht es wieder."""
        known = self._stopped_ports.get(host.id, {})
        current = {short_id for _, short_id in stopped}
        known = {cid: ports for cid, ports in known.items() if cid in current}
        missing = [cid for cid in sorted(current) if cid not in known][:MAX_INSPECT_PER_HOST]
        if missing:
            try:
                result = await self._ctx.exec.run(host, commands.port_bindings(missing), timeout_s=INSPECT_TIMEOUT_S)
            except Exception:  # noqa: BLE001 - die Liste der Container ist wichtiger als die Ports gestoppter
                result = None
            if result is not None:
                # Auch bei Exit != 0 (ein Container verschwand zwischen `ps` und `inspect`) gelten die lesbaren Zeilen;
                # was fehlt, wird beim nächsten Aufruf wieder versucht.
                parsed = parse_port_bindings(result.stdout)
                known.update({cid: parsed[cid] for cid in missing if cid in parsed})
        self._stopped_ports[host.id] = known
        for row, short_id in stopped:
            if short_id in known and not row.get("published_ports"):
                row["published_ports"] = known[short_id]

    async def measure(self) -> dict[str, Any]:
        """CPU/RAM je laufendem Container (`data`, Schlüssel wie in `list_services()`), Neustarts je Container (`restarts`)
        und je Host, was Docker dort an CPU und Arbeitsspeicher sieht (`hosts`). Docker wartet eine Messperiode ab, das
        dauert je Host 2 bis 3 s; ein Host, der nicht antwortet, fehlt einfach (die Liste zeigt seinen Fehler schon)."""
        from .operate import docker_hosts

        stats: dict[str, dict[str, Any]] = {}
        restarts: dict[str, int] = {}
        environments: dict[str, dict[str, Any]] = {}
        for host, _ in await docker_hosts(self._ctx):
            try:
                result = await self._ctx.exec.run(host, commands.stats(), timeout_s=20)
            except Exception:  # noqa: BLE001 - ein kaputter Host darf die anderen nicht verdecken
                continue
            if result.exit_code != 0:
                continue
            for line in result.stdout.splitlines():
                parsed = parse_stats_line(line)
                if parsed is not None:
                    name, values = parsed
                    stats[f"{host.id}:{name}"] = values
            try:
                counted = await self._ctx.exec.run(host, commands.restart_counts(), timeout_s=15)
            except Exception:  # noqa: BLE001 - ohne Zähler bleibt die Liste brauchbar
                counted = None
            # Auch bei Exit != 0 (ein Container verschwand zwischen `ps` und `inspect`) gelten die lesbaren Zeilen.
            if counted is not None:
                restarts.update({f"{host.id}:{name}": n for name, n in parse_restart_counts(counted.stdout).items()})
            environment = await self._environment(host)
            if environment is not None:
                environments[host.id] = environment
        return {"data": stats, "restarts": restarts, "hosts": environments}

    async def _environment(self, host: Any) -> dict[str, Any] | None:
        remembered = self._environments.get(host.id)
        if remembered is not None and time.monotonic() - remembered[0] < ENVIRONMENT_TTL_S:
            return remembered[1]
        try:
            info = await self._ctx.exec.run(host, commands.info(), timeout_s=15)
        except Exception:  # noqa: BLE001 - die Messwerte sind wichtiger als ihre Einordnung
            return remembered[1] if remembered else None
        if info.exit_code != 0:
            return remembered[1] if remembered else None
        try:
            virt = await self._ctx.exec.run(host, commands.virtualization(), timeout_s=5)
        except Exception:  # noqa: BLE001
            virt = None
        environment = parse_environment(info.stdout, virt.stdout if virt is not None else None)
        if environment is None:
            return remembered[1] if remembered else None
        self._environments[host.id] = (time.monotonic(), environment)
        return environment


_VERBS = {"container.start": "start", "container.stop": "stop", "container.restart": "restart", "container.remove": "rm"}
_VERB_LABEL = {"start": "starten", "stop": "stoppen", "restart": "neu starten", "rm": "löschen"}
# Zustände von `docker inspect`, in denen gelöscht werden darf (nicht `running`, `paused`, `restarting`).
_REMOVABLE_STATES = frozenset({"created", "exited", "dead"})
_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}\Z")
_PARAMS_SCHEMA = {
    "type": "object",
    "properties": {"container": {"type": "string", "pattern": _json_pattern(CONTAINER_NAME_RE)}},
    "required": ["container"],
}

def container_action_specs(tag: str = "docker", operate_tag: str | None = None) -> list[ActionSpec]:
    """Nur für Docker-Hosts (`host_tags`); der Tag ist einstellbar, deshalb eine Funktion: bei geänderter Einstellung
    meldet die Extension die Specs neu an. Starten, Stoppen und Neustarten gibt es auch im Modus „Nur bedienen“."""
    operate = [tag, operate_tag] if operate_tag else [tag]
    return [
        ActionSpec(
            action_type="container.start", label="Container starten", icon="play",
            description="Startet einen gestoppten Docker-Container auf diesem Host.",
            default_risk=Risk.LOW, permissions=["hosts.execute"], host_bound=True, params_schema=_PARAMS_SCHEMA,
            host_tags=operate,
        ),
        ActionSpec(
            action_type="container.stop", label="Container stoppen", icon="square",
            description="Stoppt einen laufenden Docker-Container auf diesem Host.",
            default_risk=Risk.MEDIUM, permissions=["hosts.execute"], host_bound=True, params_schema=_PARAMS_SCHEMA,
            confirm_text="Der Dienst ist danach nicht mehr erreichbar, bis er wieder gestartet wird. Fortfahren?",
            host_tags=operate,
        ),
        ActionSpec(
            action_type="container.restart", label="Container neu starten", icon="rotate-cw",
            description="Startet einen Docker-Container auf diesem Host neu.",
            default_risk=Risk.MEDIUM, permissions=["hosts.execute"], host_bound=True, params_schema=_PARAMS_SCHEMA,
            host_tags=operate,
        ),
        ActionSpec(
            action_type="container.remove", label="Container löschen", icon="trash-2",
            description="Löscht einen gestoppten Docker-Container (docker rm). Volumes und das Image bleiben.",
            default_risk=Risk.HIGH, permissions=["hosts.execute"], host_bound=True, params_schema=_PARAMS_SCHEMA,
            host_tags=[tag],
            confirm_text="Der Container wird gelöscht. Volumes und das Image bleiben erhalten, aber Daten, die nur im "
            "Container selbst lagen, sind weg. Fortfahren?",
        ),
        # Speicher aufräumen: feste Befehle, kein Nutzertext, deshalb kein command_field.
        ActionSpec(
            action_type="docker.prune_images", label="Verwaiste Images entfernen", icon="trash-2",
            description="Entfernt Images ohne Namen, die kein Container mehr nutzt (docker image prune).",
            default_risk=Risk.LOW, permissions=["hosts.execute"], host_bound=True, host_tags=[tag],
        ),
        ActionSpec(
            action_type="docker.prune_unused_images", label="Ungenutzte Images entfernen", icon="trash-2",
            description="Entfernt alle Images, die kein Container nutzt -- auch benannte (docker image prune -a).",
            default_risk=Risk.MEDIUM, permissions=["hosts.execute"], host_bound=True, host_tags=[tag],
            confirm_text="Entfernt auch benannte Images ohne Container. Werden sie wieder gebraucht, lädt Docker sie neu herunter. Fortfahren?",
        ),
        ActionSpec(
            action_type="docker.stack_restart", label="Stack neu starten", icon="rotate-cw",
            description="Startet alle Container eines Docker-Compose-Projekts neu (docker compose -p ... restart).",
            default_risk=Risk.MEDIUM, permissions=["hosts.execute"], host_bound=True, host_tags=[tag],
            params_schema={"type": "object", "properties": {"project": {"type": "string", "pattern": _json_pattern(COMPOSE_PROJECT_RE)}}, "required": ["project"]},
            confirm_text="Alle Container des Stacks starten neu -- die Dienste sind kurz nicht erreichbar. Fortfahren?",
        ),
        ActionSpec(
            action_type="docker.prune_build_cache", label="Build-Cache leeren", icon="eraser",
            description="Leert den Zwischenspeicher von docker build (docker builder prune).",
            default_risk=Risk.MEDIUM, permissions=["hosts.execute"], host_bound=True, host_tags=[tag],
            confirm_text="Der nächste Build auf diesem Host dauert dann deutlich länger. Fortfahren?",
        ),
    ]


CONTAINER_ACTION_SPECS = container_action_specs()


async def _checked(ctx: ExtensionContext, host: Any, refs: list[str], duration_ms: int | None, done: str) -> ActionResult:
    """Prüfung nach einem erfolgreichen Start/Neustart (`after_check`): Läuft der Container danach nicht stabil, ist die
    Aktion fehlgeschlagen, denn der Befehl lief, das Ziel nicht."""
    from . import after_check as ac  # spät: after_check braucht dieses Modul

    started = time.monotonic()
    verdict, _snap = await ac.check_running(ctx, host, refs)
    total_ms = (duration_ms or 0) + int((time.monotonic() - started) * 1000)
    line = ac.result_line(verdict)
    detail = {"check": {"ok": verdict.ok, "text": verdict.text}}
    if verdict.ok:
        return ActionResult(success=True, exit_code=0, output=f"{done}\n{line}", duration_ms=total_ms, detail=detail)
    return ActionResult(success=False, exit_code=0, output=done, error=line, duration_ms=total_ms, detail=detail)


class DockerMaintenanceExecutor:
    """Aufräumen (`docker.prune_*`) und Stack-Neustart, nur nach dem Gate und mit festen Befehlen."""

    action_types = frozenset(commands.PRUNE) | {"docker.stack_restart"}

    def __init__(self, ctx: "ExtensionContext", locks: StackLocks | None = None) -> None:
        self._ctx = ctx
        self._locks = locks or StackLocks()

    async def execute(self, req: ActionRequest) -> ActionResult:
        if req.action_type == "docker.stack_restart":
            project = str(req.payload.get("project") or "")
            if not COMPOSE_PROJECT_RE.match(project):
                return ActionResult(success=False, error=f"Ungültiger Projektname: {project!r}")
            host = await self._ctx.hosts.get(req.host_ref) if req.host_ref else None
            if host is None:
                return ActionResult(success=False, error="Host nicht gefunden.")
            with self._locks.hold(host.id, project, "Neustart") as busy:
                if busy is not None:
                    return ActionResult(success=False, error=busy_text(busy))
                return await self._restart(host, project)
        command = commands.prune(req.action_type)
        if command is None:
            return ActionResult(success=False, error=f"Unbekannte Aktion '{req.action_type}'.")
        if not req.host_ref:
            return ActionResult(success=False, error="Aktion ohne Host-Referenz.")
        host = await self._ctx.hosts.get(req.host_ref)
        if host is None:
            return ActionResult(success=False, error=f"Host '{req.host_ref}' nicht gefunden.")
        result = await self._ctx.exec.run(host, command, timeout_s=600)
        # Docker schließt mit "Total reclaimed space: 1.2GB": das ist die Nachricht.
        reclaimed = next((line for line in reversed(result.stdout.splitlines()) if "reclaimed" in line.lower()), None)
        return ActionResult(
            success=result.exit_code == 0,
            exit_code=result.exit_code,
            output=(f"Freigegeben: {reclaimed.split(':', 1)[1].strip()}" if reclaimed and ":" in reclaimed else result.stdout.strip() or None),
            error=result.stderr.strip() or None,
            duration_ms=result.duration_ms,
        )

    async def _restart(self, host: Any, project: str) -> ActionResult:
        from . import after_check as ac  # spät: after_check braucht dieses Modul

        # Welche Container liefen vorher? Nur die müssen danach wieder laufen: ein Container für einmalige Aufgaben
        # (Migration, Init) endet auch nach dem Neustart wieder, das ist kein Fehler.
        before = await self._ctx.exec.run(host, ac.cmd_stack_running(project), timeout_s=ac.CHECK_CALL_TIMEOUT_S)
        running = [line.strip() for line in before.stdout.splitlines() if _CONTAINER_ID_RE.match(line.strip())]
        # Ohne Compose-Datei: Compose v2+ findet die Container über ihre Labels (geprüft mit Compose 5.5.1).
        result = await self._ctx.exec.run(host, commands.compose_restart(project), timeout_s=300)
        if result.exit_code == 0 and running and before.exit_code == 0:
            return await _checked(self._ctx, host, running, result.duration_ms, f"Stack '{project}' neu gestartet.")
        skipped = (
            "Prüfung danach übersprungen: vorher lief kein Container des Stacks." if before.exit_code == 0
            else "Prüfung danach nicht möglich: die laufenden Container ließen sich vorher nicht lesen."
        )
        return ActionResult(
            success=result.exit_code == 0, exit_code=result.exit_code,
            output=f"Stack '{project}' neu gestartet.\n{skipped}" if result.exit_code == 0 else None,
            error=scrub_compose_output(result.stderr).strip() or None, duration_ms=result.duration_ms,
        )

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        return DryRunReport(would_change=True, summary=f"Würde ausführen: {commands.prune(req.action_type) or '?'}")


class DockerContainerExecutor:
    """Start/Stop/Neustart/Löschen einzelner Container (`ActionExecutor`), nur nach dem Gate. Der Befehl entsteht aus
    einem festen Verb und einem gegen Dockers Namensregel geprüften Namen, ohne freien Befehlstext."""

    action_types = frozenset(_VERBS)

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx

    async def execute(self, req: ActionRequest) -> ActionResult:
        verb = _VERBS.get(req.action_type)
        if verb is None:
            return ActionResult(success=False, error=f"Unbekannte Aktion '{req.action_type}'.")
        name = str(req.payload.get("container") or "")
        if not CONTAINER_NAME_RE.match(name):
            return ActionResult(success=False, error=f"Ungültiger Containername: {name!r}")
        if not req.host_ref:
            return ActionResult(success=False, error="Aktion ohne Host-Referenz.")
        host = await self._ctx.hosts.get(req.host_ref)
        if host is None:
            return ActionResult(success=False, error=f"Host '{req.host_ref}' nicht gefunden.")

        if verb == "stop":
            inspected = await self._ctx.exec.run(host, commands.container_id(name), timeout_s=15)
            if inspected.exit_code == 0 and is_own_container(inspected.stdout.strip()):
                return ActionResult(
                    success=False,
                    error=f"'{name}' ist der Container, in dem Nodvard Deck selbst läuft -- Stoppen würde "
                    "diese Oberfläche abschalten. Neustarten geht, Stoppen nur direkt auf dem Host.",
                )
        target = name
        if verb == "rm":
            refusal, container_id = await self._check_remove(host, name)
            if refusal is not None:
                return ActionResult(success=False, error=refusal)
            # Die volle ID aus der Prüfung, nicht noch einmal der Name: ein Container, der inzwischen unter diesem Namen
            # neu angelegt wurde, bleibt unberührt.
            target = container_id

        # `docker rm` ohne `-f` und ohne `-v`: Docker selbst lehnt einen laufenden Container ab (auch wenn er zwischen
        # Prüfung und Befehl gestartet wurde), Volumes bleiben in jedem Fall stehen.
        result = await self._ctx.exec.run(host, commands.container_verb(verb, target), timeout_s=120)
        if result.exit_code == 0 and verb in ("start", "restart"):
            return await _checked(self._ctx, host, [name], result.duration_ms, f"Container '{name}' "
                                  + ("gestartet." if verb == "start" else "neu gestartet."))
        return ActionResult(
            success=result.exit_code == 0,
            exit_code=result.exit_code,
            output=result.stdout or None,
            error=result.stderr.strip() or None,
            duration_ms=result.duration_ms,
        )

    async def _check_remove(self, host: Any, name: str) -> tuple[str | None, str]:
        """(Grund, warum `name` nicht gelöscht wird, oder None; volle ID des Containers). Nur gestoppte Container, nie
        Nodvard Deck selbst. Lässt sich der Container nicht eindeutig lesen, wird nichts gelöscht."""
        inspected = await self._ctx.exec.run(host, commands.container_id_state(name), timeout_s=15)
        if inspected.exit_code != 0:
            detail = inspected.stderr.strip()
            return (f"Container '{name}' nicht gefunden." if "No such" in detail else (detail or "docker inspect fehlgeschlagen.")), ""
        container_id, _, state = inspected.stdout.strip().partition("|")
        state = state.strip()
        if not _CONTAINER_ID_RE.match(container_id):
            return f"Zustand von '{name}' nicht lesbar -- nichts gelöscht.", ""
        if is_own_container(container_id):
            return f"'{name}' ist der Container, in dem Nodvard Deck selbst läuft -- löschen geht nur direkt auf dem Host.", ""
        if state in ("running", "paused", "restarting"):
            return f"'{name}' läuft noch ({state}). Erst stoppen, dann löschen.", ""
        if state not in _REMOVABLE_STATES:
            return f"Zustand von '{name}' nicht lesbar ({state or 'leer'}) -- nichts gelöscht.", ""
        return None, container_id

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        verb = _VERBS.get(req.action_type, "?")
        name = req.payload.get("container", "?")
        summary = f"Würde Container '{name}' {_VERB_LABEL.get(verb, verb)}."
        if verb == "rm":
            summary += " Nur wenn er gestoppt ist; Volumes und das Image bleiben."
        return DryRunReport(would_change=True, summary=summary)
