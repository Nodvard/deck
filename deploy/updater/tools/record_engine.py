"""Inspect-Antworten einer echten Engine aufzeichnen -- fuer die Testdaten unter `tests/fixtures/engines/`.

Nicht Teil des Helfers: liegt ausserhalb des Pakets und kommt nicht ins Image. Liest **nur** (`GET`), ueber den
Client des Helfers (`nodvard_deck_updater.engine`, also mit dessen Allowlist), und prueft zusaetzlich jede Anfrage
auf `GET`.

Aufgezeichnet werden fuer ein Compose-Projekt: `/_ping` (nur `Api-Version`), `/version`, die Container-Liste des
Projekts, das Inspect jedes Containers, seines Images und seiner Netze. Mit `--api 1.43` fragt das Werkzeug mit
dieser API-Version (der Docker-Dienst antwortet dann im Format dieser Version, z. B. wie Synology).

**Nur ein Testprojekt aufzeichnen**, mit neutralem Namen und am besten auf einem Wegwerf-Rechner -- nie das echte
Projekt eines Servers. Die Aufnahme kommt in ein oeffentliches Repository.

**Bereinigt** (Geheimnisse und Angaben zum Rechner koennen dort stehen). Geheimnisse: Werte in `Env`, Label-Werte
ausser `com.docker.compose.*` und `org.opencontainers.image.*`, Optionen des Log-Treibers, die Argumente von
`Healthcheck.Test`, `Cmd` und `Args`, die Ausgaben in `State.Health.Log`. Jeder Wert wird durch ein Kennzeichen
`scrubbed-<n>` ersetzt; gleiche Werte bekommen dasselbe Kennzeichen, damit Vergleiche (Container-Wert gleich
Image-Wert) erhalten bleiben. Leere Werte bleiben leer. Angaben zum Rechner:

* **Pfade** auf dem Rechner (die Compose-Labels `...working_dir`, `...config_files` und `...environment_file`, die
  Quellen von `Binds` und `Mounts` (ein Volume-Name als Quelle bleibt), `LogPath`, `ResolvConfPath`,
  `GraphDriver`-Ordner, `device`-Optionen): jeder Ordnername wird durch `d<n>` ersetzt (gleicher Name, gleiches
  Kennzeichen; die Verschachtelung bleibt, ein Pfad ist weiter Vorsatz eines anderen). Unveraendert bleiben
  `/var/lib/docker/...` (der Standardordner des Docker-Dienstes, darin stehen nur Namen von Volumes), der
  Docker-Socket und allgemeine Systemordner.
* **Adressen:** IPv4 ausser `127.x` und `0.0.0.0` -> `198.18.x.y` (dasselbe /24-Netz bleibt dasselbe Netz, die letzte
  Stelle bleibt), IPv6 -> `2001:db8::<n>`, MAC -> `02:00:00:00:x:y`.
* **Rechnernamen:** `Hostname` (ausser der Kurz-ID des Containers, das ist der Standard; auch dort, wo `DNSNames` und
  `Aliases` eines Netzes ihn wiederholen), `Domainname`, `DnsSearch` und die Namen in `ExtraHosts` -> `host-<n>`.

**Nicht bereinigt** sind die Namen von Projekt, Containern, Diensten, Netzen, Volumes und Images sowie die Namen
von Labels und Umgebungsvariablen. Sie stehen als Schluessel, in `NetworkMode`, in `Binds`, in Quellpfaden und in
den Namen der Container zugleich; eine Ersetzung per Textsuche zerstoerte die Zusammenhaenge, auf die `clone.py`
sich verlaesst (und trafe ein Volume namens `data` auch `/app/data`). Darum gilt der Hinweis oben.

**Gegenprobe:** Nach dem Bereinigen sucht `find_leaks` die Aufnahme nach dem ab, was nicht hineingehoert (Adressen
aus 10.x, 100.64-100.127, 192.168.x und 169.254.x, echte IPv6-Adressen, MAC-Adressen von Geraeten, Heimatordner
`/home`, `/Users`, `/root`). Findet sie etwas, wird nichts geschrieben. Der Test `test_fixtures_are_scrubbed` in
`tests/test_clone.py` prueft mit derselben Suche (und eigenen Mustern) alle eingecheckten Aufnahmen.

Aufruf (im Repo-Wurzelordner, als Nutzer mit Zugriff auf den Socket):

    python deploy/updater/tools/record_engine.py --project u2probe --name docker29-api154
    python deploy/updater/tools/record_engine.py --project u2probe --name docker29-api143 --api 1.43
    python deploy/updater/tools/record_engine.py --golden     # Golden-Dateien *.clone.json neu erzeugen
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from pathlib import Path
from typing import Any

UPDATER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UPDATER_DIR))

from nodvard_deck_updater import engine as engine_mod  # noqa: E402 - Paket liegt neben tools/, nicht installiert
from nodvard_deck_updater import policy  # noqa: E402

KEEP_LABEL_PREFIXES = ("com.docker.compose.", "org.opencontainers.image.")
PATH_LABEL_SUFFIXES = (".working_dir", ".config_files", ".environment_file")
"""Compose-Labels, deren Wert Pfade auf dem Rechner sind (`config_files` als Liste mit Komma)."""
PATH_KEYS = frozenset({"ResolvConfPath", "HostnamePath", "HostsPath", "LogPath", "SandboxKey", "LowerDir",
                       "MergedDir", "UpperDir", "WorkDir", "device"})
KEEP_PATH_PREFIX = "/var/lib/docker/"
KEEP_PATH_PARTS = frozenset({".", "..", "var", "run", "lib", "docker", "docker.sock", "tmp", "dev", "proc", "sys",
                             "etc"})
KEEP_HOSTS = frozenset({"localhost", "host.docker.internal", "host-gateway"})
DEFAULT_OUT = UPDATER_DIR / "tests" / "fixtures" / "engines"

PRIVATE_NETWORKS = tuple(ipaddress.ip_network(net) for net in
                         ("10.0.0.0/8", "100.64.0.0/10", "169.254.0.0/16", "192.168.0.0/16"))
"""Was in einer Aufnahme nicht stehen darf (`find_leaks`). Der Bereich 172.16.0.0/12 fehlt mit Absicht: das sind die
Standardnetze des Docker-Dienstes, die auch ein Testprojekt bekommt."""
DOCUMENTATION_V6 = ipaddress.ip_network("2001:db8::/32")

_PART_RE = re.compile(r"[^\\/]+")
_BIND_RE = re.compile(r"^(?P<src>/[^:]*|[A-Za-z]:\\[^:]*):(?P<rest>.*)$", re.DOTALL)
_IPV4_RE = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9]|\.[0-9])", re.ASCII)
_IPV6_RE = re.compile(r"[0-9A-Fa-f:.]*:[0-9A-Fa-f:.]*")
_MAC_RE = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])")
_HOME_RE = re.compile(r"(?<![\w.-])/(?:home|Users|root)(?![\w.-])")
_SHORT_ID_RE = re.compile(r"[0-9a-f]{12}")


class Scrubber:
    """Ersetzt Geheimnisse durch `scrubbed-<n>` (gleicher Wert -> gleiches Kennzeichen) und Angaben zum Rechner (Pfade,
    Adressen, Rechnernamen) durch neutrale Werte, die Zusammenhaenge erhalten (siehe Kopf der Datei)."""

    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}
        self.seen: dict[str, dict[str, int]] = {}

    def token(self, value: str) -> str:
        if value == "":
            return ""
        if value not in self.tokens:
            self.tokens[value] = f"scrubbed-{len(self.tokens) + 1}"
        return self.tokens[value]

    def _index(self, kind: str, value: str) -> int:
        """Laufende Nummer (ab 1) des Werts in seiner Art; derselbe Wert bekommt immer dieselbe."""
        seen = self.seen.setdefault(kind, {})
        if value not in seen:
            seen[value] = len(seen) + 1
        return seen[value]

    def env(self, entries: list[Any]) -> list[Any]:
        out = []
        for entry in entries:
            if isinstance(entry, str) and "=" in entry:
                key, _, value = entry.partition("=")
                out.append(f"{key}={self.token(value)}")
            else:
                out.append(self.token(entry) if isinstance(entry, str) else entry)
        return out

    def labels(self, labels: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for key, value in labels.items():
            if not isinstance(value, str):
                out[key] = value
            elif key.startswith("com.docker.compose.") and key.endswith(PATH_LABEL_SUFFIXES):
                out[key] = ",".join(self.path(part) for part in value.split(","))
            elif key.startswith(KEEP_LABEL_PREFIXES):
                out[key] = value
            else:
                out[key] = self.token(value)
        return out

    # --- Angaben zum Rechner -------------------------------------------------

    def path(self, value: str) -> str:
        """Jeder Ordner- und Dateiname wird zu `d<n>`; Trenner und Verschachtelung bleiben."""
        if value.startswith(KEEP_PATH_PREFIX):
            return value

        def part(match: re.Match[str]) -> str:
            name = match.group(0)
            return name if name in KEEP_PATH_PARTS else f"d{self._index('path', name)}"

        return _PART_RE.sub(part, value)

    def source(self, value: str) -> str:
        """`Source` eines Mounts: ein Pfad wird ersetzt, ein Volume-Name (ohne Trenner, `HostConfig.Mounts` mit
        `Type: volume`) bleibt wie in `Mounts[].Name` und `Binds`."""
        return self.path(value) if "/" in value or "\\" in value else self.text(value)

    def bind(self, entry: str) -> str:
        """`quelle:ziel[:optionen]`: nur eine Quelle, die ein Pfad ist, wird ersetzt (ein Volume-Name bleibt)."""
        match = _BIND_RE.match(entry)
        return f"{self.path(match['src'])}:{match['rest']}" if match else self.text(entry)

    def host(self, value: str) -> str:
        return value if value == "" or value in KEEP_HOSTS else f"host-{self._index('host', value)}"

    def hostname(self, value: str) -> str:
        """`Config.Hostname`: die Kurz-ID des Containers ist der Standard und bleibt."""
        return value if _SHORT_ID_RE.fullmatch(value) else self.host(value)

    def remember_hostname(self, container: dict[str, Any]) -> None:
        """Merkt sich den `Hostname` eines Inspects vorab: `DNSNames` nennt ihn noch einmal, auch vor `Config`."""
        config = container.get("Config")
        if isinstance(config, dict) and isinstance(config.get("Hostname"), str):
            self.hostname(config["Hostname"])

    def dns_names(self, names: list[Any]) -> list[Any]:
        """`DNSNames` (ab API 1.44) und `Aliases` eines Netzes: Containername, Aliases, Kurz-ID und der `Hostname`
        (bei aelteren API-Versionen haengt Docker die Kurz-ID und den eigenen Hostname eines Containers in
        benutzerdefinierten Netzen auch an `Aliases` an, so in den Aufnahmen mit 1.41 und 1.43; ein alter
        Docker-Dienst ohne `DNSNames` nennt ihn nur dort). Nur ein schon bekannter Rechnername wird ersetzt, die
        Namen der Container bleiben (siehe Kopf der Datei)."""
        hosts = self.seen.get("host", {})
        return [f"host-{hosts[name]}" if isinstance(name, str) and name in hosts else self.walk(name) for name in names]

    def extra_host(self, entry: str) -> str:
        name, sep, address = entry.partition(":")
        if not sep:
            return self.host(entry)
        return f"{self.host(name)}:{address if address in KEEP_HOSTS else self.text(address)}"

    def ipv4(self, text: str) -> str:
        try:
            address = ipaddress.IPv4Address(text)
        except ValueError:
            return text
        if address.is_loopback or address.is_unspecified:
            return text
        net = self._index("net", text.rpartition(".")[0]) - 1  # dasselbe /24-Netz bleibt dasselbe Netz
        return f"198.{18 + (net >> 8)}.{net & 255}.{text.rpartition('.')[2]}"

    def ipv6(self, text: str) -> str:
        try:
            address = ipaddress.IPv6Address(text)
        except ValueError:
            return text
        return text if address.is_loopback or address.is_unspecified else f"2001:db8::{self._index('v6', text):x}"

    def mac(self, text: str) -> str:
        number = self._index("mac", text.lower())
        return f"02:00:00:00:{number >> 8:02x}:{number & 255:02x}"

    def text(self, value: str) -> str:
        """Adressen in beliebigem Text (MAC, IPv4, IPv6) ersetzen; was keine gueltige Adresse ist, bleibt."""
        value = _MAC_RE.sub(lambda match: self.mac(match.group(0)), value)
        value = _IPV4_RE.sub(lambda match: self.ipv4(match.group(0)), value)
        return _IPV6_RE.sub(lambda match: self.ipv6(match.group(0)), value)

    # --- Durchlauf -----------------------------------------------------------

    def walk(self, obj: Any, key: str | None = None) -> Any:
        if isinstance(obj, dict):
            self.remember_hostname(obj)
            out = {}
            for k, v in obj.items():
                dest = self.text(k) if isinstance(k, str) else k  # auch Schluessel koennen Adressen sein (`Subnets`)
                if k == "Env" and isinstance(v, list):
                    out[dest] = self.env(v)
                elif k == "Labels" and isinstance(v, dict):
                    out[dest] = self.labels(v)
                elif k == "LogConfig" and isinstance(v, dict):
                    config = v.get("Config")
                    out[dest] = {**v, "Config": {ck: self.token(cv) if isinstance(cv, str) else cv
                                              for ck, cv in config.items()} if isinstance(config, dict) else config}
                elif k == "Health" and isinstance(v, dict):
                    out[dest] = {**v, "Log": [{**e, "Output": ""} if isinstance(e, dict) else e
                                           for e in v.get("Log") or []]}
                elif k == "Healthcheck" and isinstance(v, dict) and isinstance(v.get("Test"), list) and v["Test"]:
                    test = v["Test"]
                    out[dest] = {**v, "Test": [test[0], *(self.token(t) if isinstance(t, str) else t
                                                          for t in test[1:])]}
                elif k in ("Cmd", "Args") and isinstance(v, list):
                    out[dest] = [self.token(item) if isinstance(item, str) else item for item in v]
                elif k == "Binds" and isinstance(v, list):
                    out[dest] = [self.bind(item) if isinstance(item, str) else item for item in v]
                elif k == "ExtraHosts" and isinstance(v, list):
                    out[dest] = [self.extra_host(item) if isinstance(item, str) else item for item in v]
                elif k == "DnsSearch" and isinstance(v, list):
                    out[dest] = [self.host(item) if isinstance(item, str) else item for item in v]
                elif k == "Hostname" and isinstance(v, str):
                    out[dest] = self.hostname(v)
                elif k == "Domainname" and isinstance(v, str):
                    out[dest] = self.host(v)
                elif k in ("DNSNames", "Aliases") and isinstance(v, list):
                    out[dest] = self.dns_names(v)
                elif k == "Source" and isinstance(v, str):
                    out[dest] = self.source(v)
                elif k in PATH_KEYS and isinstance(v, str):
                    out[dest] = self.path(v)
                else:
                    out[dest] = self.walk(v, k)
            return out
        if isinstance(obj, list):
            return [self.walk(item, key) for item in obj]
        if isinstance(obj, str):
            return self.text(obj)
        return obj


def leak_kinds(text: str) -> list[str]:
    """Was in `text` nicht in eine eingecheckte Aufnahme gehoert (leer, wenn nichts auffaellt)."""
    kinds = []
    for match in _IPV4_RE.finditer(text):
        try:
            address = ipaddress.IPv4Address(match.group(0))
        except ValueError:
            continue
        if any(address in net for net in PRIVATE_NETWORKS):
            kinds.append("private IPv4-Adresse")
            break
    for match in _IPV6_RE.finditer(text):
        try:
            address6 = ipaddress.IPv6Address(match.group(0))
        except ValueError:
            continue
        if not (address6.is_loopback or address6.is_unspecified or address6 in DOCUMENTATION_V6):
            kinds.append("IPv6-Adresse")
            break
    for match in _MAC_RE.finditer(text):
        if not int(match.group(0)[:2], 16) & 2:  # Docker vergibt "lokal verwaltete" MACs (02:42:...), Geraete nicht
            kinds.append("MAC-Adresse eines Geraets")
            break
    if _HOME_RE.search(text):
        kinds.append("Heimatordner (/home, /Users, /root)")
    return kinds


def find_leaks(obj: Any, where: str = "$") -> list[str]:
    """Alle Stellen einer Aufnahme (Schluessel und Werte), die `leak_kinds` beanstandet -- als Ort und Art, ohne den
    Wert selbst."""
    found: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            found += [f"{where}.{key} (Schluessel): {kind}" for kind in leak_kinds(str(key))]
            found += find_leaks(value, f"{where}.{key}")
    elif isinstance(obj, list):
        for number, item in enumerate(obj):
            found += find_leaks(item, f"{where}[{number}]")
    elif isinstance(obj, str):
        found += [f"{where}: {kind}" for kind in leak_kinds(obj)]
    return found


def read_only_engine(socket_path: str) -> engine_mod.Engine:
    """Der Client des Helfers, der zusaetzlich jede Anfrage ausser `GET` verweigert."""
    engine = engine_mod.Engine(socket_path)
    real_request = engine._request

    def get_only(method: str, pattern: str, **kwargs: Any) -> Any:
        if method != "GET":
            raise SystemExit(f"nur GET erlaubt, nicht {method} {pattern}")
        return real_request(method, pattern, **kwargs)

    engine._request = get_only  # type: ignore[method-assign]
    return engine


def record(socket_path: str, project: str, api: str | None, extra_images: list[str]) -> dict[str, Any]:
    engine = read_only_engine(socket_path)
    _, headers, _ = engine._request("GET", "/_ping", versioned=False, expect_json=False)
    engine.negotiate()
    if api is not None:
        forced = engine_mod.parse_api_version(api)
        if forced is None or not engine_mod.API_MIN <= forced <= engine_mod.API_MAX:
            raise SystemExit("--api ausserhalb von 1.41 bis 1.54")
        engine.api_version = forced
    version = engine._json("GET", "/version")
    listing = engine.list_containers([f"com.docker.compose.project={project}"])
    inspect: dict[str, Any] = {}
    images: dict[str, Any] = {}
    networks: dict[str, Any] = {}
    for item in listing:
        container = engine.inspect_container(item["Id"])
        inspect[container["Id"]] = container
        image_id = container.get("Image")
        if policy.is_image_id(image_id) and image_id not in images:
            images[image_id] = engine.inspect_image(image_id)
        for endpoint in ((container.get("NetworkSettings") or {}).get("Networks") or {}).values():
            network_id = endpoint.get("NetworkID")
            if policy.is_container_id(network_id) and network_id not in networks:
                networks[network_id] = engine.inspect_network(network_id)
    for image_id in extra_images:
        if not policy.is_image_id(image_id):
            raise SystemExit("--image nur als Image-ID sha256:<64 hex>")
        images[image_id] = engine.inspect_image(image_id)
    scrub = Scrubber()
    data = {
        "_doc": ("Aufgezeichnet mit deploy/updater/tools/record_engine.py (nur GET, bereinigt: Env-Werte, Label-Werte "
                 "ausser com.docker.compose.*/org.opencontainers.image.*, Log-Optionen, Healthcheck-Argumente, Cmd, "
                 "Args, Health.Log; Pfade, Adressen und Rechnernamen durch neutrale Werte ersetzt)."),
        "project": project,
        "api": "{}.{}".format(*engine.api_version),
        "engine": {"Version": version.get("Version"), "ApiVersion": version.get("ApiVersion"),
                   "MinAPIVersion": version.get("MinAPIVersion")},
        "ping": {"Api-Version": headers.get("Api-Version")},
        "version": scrub.walk(version),
        "containers": scrub.walk(listing),
        "inspect": scrub.walk(inspect),
        "images": scrub.walk(images),
        "networks": scrub.walk(networks),
    }
    leaks = find_leaks(data)
    if leaks:
        shown = "\n  ".join(leaks[:10])
        raise SystemExit(f"Nichts geschrieben: die Aufnahme enthaelt noch Angaben zum Rechner ({len(leaks)} Stellen), "
                         f"z. B.:\n  {shown}\nDas Projekt so aendern, dass sie nicht vorkommen (Testprojekt auf einem "
                         "Wegwerf-Rechner), und neu aufzeichnen.")
    return data


GOLDEN_NAMES = ("docker29-api141", "docker29-api143", "docker29-api154", "docker29-api154-stack",
                "docker29-api154-anon")


def write_goldens(out: Path) -> list[Path]:
    """Erzeugt `<name>.clone.json` (erwarteter `create`-Body und `connect`-Liste) aus den Aufzeichnungen neu --
    ohne Engine. Danach den Unterschied pruefen: jede Aenderung am Body muss gewollt sein."""
    from nodvard_deck_updater import clone

    written = []
    for name in GOLDEN_NAMES:
        data = json.loads((out / f"{name}.json").read_text(encoding="ascii"))
        target = next(c for c in data["inspect"].values()
                      if c["Config"]["Labels"].get("com.docker.compose.service") == "nodvard-deck")
        new_image_id = next(i for i, image in data["images"].items()
                            if (image["Config"].get("Labels") or {}).get(policy.VERSION_LABEL) == "0.7.1")
        plan = clone.build(target, data["images"][target["Image"]], new_image_id=new_image_id,
                           api_version=engine_mod.parse_api_version(data["api"]),
                           network_drivers={nid: n["Driver"] for nid, n in data["networks"].items()})
        golden = {
            "_doc": f"Erwarteter create-Body und connect-Liste fuer {name}.json (Golden-Test in test_clone.py; bei "
                    "einer gewollten Aenderung von clone.py neu erzeugen und den Unterschied pruefen).",
            "new_image_id": new_image_id, "name": plan.name, "body": plan.body,
            "connects": [list(c) for c in plan.connects],
        }
        path = out / f"{name}.clone.json"
        path.write_text(json.dumps(golden, indent=1, sort_keys=True, ensure_ascii=True) + "\n", encoding="ascii")
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--golden", action="store_true",
                        help="nur die Golden-Dateien *.clone.json aus den Aufzeichnungen neu erzeugen (ohne Engine)")
    parser.add_argument("--project", help="Compose-Projekt (Label com.docker.compose.project)")
    parser.add_argument("--name", help="Dateiname ohne .json, z. B. docker29-api154")
    parser.add_argument("--api", help="API-Version erzwingen (1.41 bis 1.54), sonst ausgehandelt")
    parser.add_argument("--image", action="append", default=[], help="weiteres Image (ID), z. B. das neue")
    parser.add_argument("--socket", default=engine_mod.SOCKET_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if args.golden:
        for path in write_goldens(args.out):
            print(path)
        return 0
    if not args.project or not args.name:
        parser.error("--project und --name sind noetig (oder --golden)")
    data = record(args.socket, args.project, args.api, args.image)
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / f"{args.name}.json"
    target.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=True) + "\n", encoding="ascii")
    print(f"{target}: {len(data['inspect'])} Container, {len(data['images'])} Images, {len(data['networks'])} Netze")
    print("Hinweis: Namen von Projekt, Containern, Netzen, Volumes und Images bleiben unveraendert -- die Datei vor "
          "dem Einchecken ansehen.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
