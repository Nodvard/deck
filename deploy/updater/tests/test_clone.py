"""Klonen und Nachkontrolle.

* **Golden-Tests** aus Inspects, die mit `tools/record_engine.py` an einer echten Engine (Docker 29.3.1,
  containerd-Store) aufgezeichnet wurden: API 1.54, 1.43 (Format wie Synology/Docker 24) und 1.41, dazu ein zweites
  Projekt mit benanntem Volume, Image ohne Tag und nur einem Netz (wie ein Portainer-Stack unter `/data/compose/3`).
* **Echte Paare** alt/neu (`*-pair.json`): der neue Container wurde an der echten Engine mit dem Body aus
  `clone.build` angelegt, verbunden und gestartet -- `clone.verify` muss beide Staende annehmen und jede
  Verfaelschung ablehnen. Die Paare entstanden **vor** der Aenderung, die veroeffentlichte Ports in
  `ExposedPorts` belaesst; ihr Body hatte also noch kein `ExposedPorts`. Mit dem heutigen Body ist das Ergebnis
  gleich, weil beide Images 8080 per `EXPOSE` erklaeren. Den heutigen Body hat noch keine echte Engine angelegt
  (die synthetischen Tests unten decken ihn ab); bei der naechsten Aufzeichnung mit `tools/record_engine.py` die
  Paare neu erzeugen.
* **Synthetisch** jede Regel aus `clone.py` (Config, HostConfig, Netze, Nachkontrolle), mit einer Nachbildung des
  Zusammenfuehrens im Docker-Dienst (`simulate_create`, nach moby `daemon/commit.go` `merge()`).
"""

from __future__ import annotations

import copy
import json
import re

import pytest
from fake_engine import FIXTURES, load_fixture
from nodvard_deck_updater import clone, policy, target
from nodvard_deck_updater.engine import Engine, NotAllowed
from nodvard_deck_updater.policy import Refusal

RECORDED = ["docker29-api141", "docker29-api143", "docker29-api154", "docker29-api154-stack", "docker29-api154-anon"]
PAIRS = ["docker29-api154-pair", "docker29-api143-pair"]
SECRET = "geheim123"  # stand bei der Aufzeichnung als U2_SECRET in der Compose-Datei

OLD_ID = "1" * 64
NEW_ID = "2" * 64
OLD_IMAGE = "sha256:" + "3" * 64
NEW_IMAGE = "sha256:" + "4" * 64
NET_A = "a" * 64
NET_B = "b" * 64
NET_BRIDGE = "c" * 64
OUTPUT_ONLY = ("Id", "Created", "Path", "Args", "State", "ResolvConfPath", "HostnamePath", "HostsPath", "LogPath",
               "Name", "RestartCount", "Driver", "Platform", "MountLabel", "ProcessLabel", "AppArmorProfile",
               "ExecIDs", "GraphDriver", "SizeRw", "SizeRootFs", "NetworkSettings", "Mounts",
               "ImageManifestDescriptor")


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def recorded(name):
    data = load_fixture(name)
    target = next(c for c in data["inspect"].values()
                  if c["Config"]["Labels"].get("com.docker.compose.service") == "nodvard-deck")
    new_image_id = next(i for i, image in data["images"].items()
                        if (image["Config"].get("Labels") or {}).get(policy.VERSION_LABEL) == "0.7.1")
    drivers = {nid: network["Driver"] for nid, network in data["networks"].items()}
    api = tuple(int(x) for x in data["api"].split("."))
    return data, target, data["images"][target["Image"]], new_image_id, drivers, api


def pair(name):
    data = load_fixture(name)
    api = tuple(int(x) for x in data["api"].split("."))
    plan = clone.build(data["old"], data["old_image"], new_image_id=data["created"]["Image"], api_version=api,
                       network_drivers=data["network_drivers"])
    return data, plan


def image_config(**config):
    base = {
        "Entrypoint": ["/entrypoint.sh"], "Cmd": ["uvicorn", "nodvard_deck.main:app"], "WorkingDir": "/app",
        "Env": ["PATH=/usr/local/bin:/usr/bin", "PYTHON_VERSION=3.12.14", "NODVARD_DECK_ENV=prod"],
        "Labels": {policy.VERSION_LABEL: "0.7.0", "org.opencontainers.image.source": "x"},
        "ExposedPorts": {"8080/tcp": {}}, "Volumes": {"/app/data": {}},
        "Healthcheck": {"Test": ["CMD-SHELL", "check"], "Interval": 30_000_000_000, "Timeout": 3_000_000_000,
                        "StartPeriod": 300_000_000_000, "Retries": 3},
    }
    base.update(config)
    return {"Id": OLD_IMAGE, "Config": base, "RepoDigests": [policy.REPOSITORY + "@sha256:" + "5" * 64]}


def container(**changes):
    """Ein Inspect wie von Compose angelegt: Image-Werte und Nutzerwerte gemischt (wie nach `merge` im Daemon)."""
    image = image_config()["Config"]
    inspect = {
        "Id": OLD_ID, "Name": "/deck-nodvard-deck-1", "Image": OLD_IMAGE,
        "Config": {
            "Hostname": OLD_ID[:12], "Domainname": "", "User": "", "AttachStdin": False, "AttachStdout": True,
            "AttachStderr": True, "ExposedPorts": {"8080/tcp": {}}, "Tty": False, "OpenStdin": False,
            "StdinOnce": False, "Env": ["NODVARD_DECK_AUDIT_RETENTION_DAYS=90", *image["Env"]],
            "Cmd": list(image["Cmd"]), "Healthcheck": {**image["Healthcheck"], "Test": ["CMD", "python", "-c", "x"]},
            "Image": policy.REPOSITORY + ":latest", "Volumes": {"/app/data": {}}, "WorkingDir": "/app",
            "Entrypoint": ["/entrypoint.sh"], "OnBuild": None,
            "Labels": {**image["Labels"], "com.docker.compose.project": "deck", "com.docker.compose.service":
                       "nodvard-deck", "com.docker.compose.oneoff": "False", "com.docker.compose.image": OLD_IMAGE,
                       "user.label": "mine"},
            "StopTimeout": 20,
        },
        "HostConfig": {
            "Binds": ["deck_updater:/app/updater:rw"], "NetworkMode": "deck_back",
            "PortBindings": {"8080/tcp": [{"HostIp": "", "HostPort": "8080"}]},
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0}, "AutoRemove": False,
            "LogConfig": {"Type": "json-file", "Config": {}}, "ShmSize": 67108864, "Runtime": "runc",
            "MaskedPaths": ["/proc/kcore"], "ReadonlyPaths": ["/proc/sys"], "ContainerIDFile": "",
            "KernelMemoryTCP": 0, "Privileged": False, "Dns": [], "Mounts": None, "CgroupnsMode": "private",
        },
        "Mounts": [
            {"Type": "volume", "Name": "deck_updater", "Source": "/var/lib/docker/v/deck_updater/_data",
             "Destination": "/app/updater", "Driver": "local", "Mode": "rw", "RW": True, "Propagation": ""},
            {"Type": "volume", "Name": "9" * 64, "Source": "/var/lib/docker/v/" + "9" * 64 + "/_data",
             "Destination": "/app/data", "Driver": "local", "Mode": "", "RW": True, "Propagation": ""},
        ],
        "NetworkSettings": {"Networks": {
            "deck_back": {"IPAMConfig": {"IPv4Address": "172.30.0.10"}, "Links": None,
                          "Aliases": ["deck-nodvard-deck-1", "nodvard-deck", OLD_ID[:12]], "MacAddress": "02:42:x",
                          "DriverOpts": None, "NetworkID": NET_B, "EndpointID": "e" * 64, "Gateway": "172.30.0.1",
                          "IPAddress": "172.30.0.10", "IPPrefixLen": 24, "IPv6Gateway": "", "GlobalIPv6Address": "",
                          "GlobalIPv6PrefixLen": 0, "DNSNames": ["nodvard-deck", OLD_ID[:12]]},
            "deck_front": {"IPAMConfig": {}, "Aliases": ["deck-nodvard-deck-1", "nodvard-deck", "deck-front"],
                           "NetworkID": NET_A, "EndpointID": "f" * 64, "DNSNames": None},
        }},
        "State": {"Status": "running", "Running": True}, "RestartCount": 0, "Path": "/entrypoint.sh",
    }
    for path, value in changes.items():
        _set(inspect, path, value)
    return inspect


def _set(obj, path, value):
    *parents, last = path.split(".")
    for key in parents:
        obj = obj[key]
    if value is _DELETE:
        obj.pop(last, None)
    else:
        obj[last] = value


_DELETE = object()
DRIVERS = {NET_A: "bridge", NET_B: "bridge", NET_BRIDGE: "bridge"}


def build(inspect=None, image=None, *, api=(1, 54), drivers=DRIVERS, new_image_id=NEW_IMAGE):
    return clone.build(inspect or container(), image or image_config(), new_image_id=new_image_id, api_version=api,
                       network_drivers=drivers)


def refusal(func, *args, **kwargs):
    with pytest.raises(Refusal) as exc:
        func(*args, **kwargs)
    return exc.value


def _key(entry):
    return entry.split("=", 1)[0]


def simulate_create(plan, new_image, new_id=NEW_ID, *, old=None, started=False, short_id_alias=False):
    """Was der Docker-Dienst aus dem Body macht (moby `merge()`): Nutzerwerte gewinnen, der Rest kommt aus dem
    neuen Image. Volumes aus `VOLUME`, die nicht eingehaengt sind, werden neue anonyme Volumes."""
    body = copy.deepcopy(plan.body)
    nic = new_image["Config"]
    config = {k: v for k, v in body.items() if k not in ("HostConfig", "NetworkingConfig")}
    env = config.get("Env") or []
    config["Env"] = env + [e for e in nic.get("Env") or [] if _key(e) not in {_key(x) for x in env}]
    config["Labels"] = {**(nic.get("Labels") or {}), **(config.get("Labels") or {})}
    if not config.get("Cmd") and not config.get("Entrypoint"):
        config["Cmd"] = nic.get("Cmd")
    config["Entrypoint"] = config.get("Entrypoint") or nic.get("Entrypoint")
    for field in ("User", "WorkingDir", "StopSignal"):
        config[field] = config.get(field) or nic.get(field, "")
    for field in ("ExposedPorts", "Volumes"):
        config[field] = {**(nic.get(field) or {}), **(config.get(field) or {})} or None
    health, image_health = config.get("Healthcheck"), nic.get("Healthcheck")
    if health is None:
        config["Healthcheck"] = image_health
    elif image_health:
        for field in clone.HEALTH_FIELDS:
            if not health.get(field) and image_health.get(field):
                health[field] = image_health[field]
    config.setdefault("Hostname", new_id[:12])
    for field in ("Domainname", "Tty", "OpenStdin", "StdinOnce", "AttachStdin"):
        config.setdefault(field, "" if field == "Domainname" else False)
    host = body["HostConfig"]
    if host.get("Privileged") is not True:  # ein privilegierter Container bekommt keine Standardlisten (moby)
        host.setdefault("MaskedPaths", ["/proc/kcore"])
        host.setdefault("ReadonlyPaths", ["/proc/sys"])
    host.setdefault("AutoRemove", False)
    mounts = []
    covered = set()
    for bind in host.get("Binds") or []:
        source, destination = bind.split(":")[0], clone.bind_destination(bind)
        kind = "bind" if source.startswith("/") else "volume"
        mounts.append({"Type": kind, "Name": source if kind == "volume" else "", "Source": source,
                       "Destination": destination, "RW": not bind.endswith(":ro")})
        covered.add(destination)
    for mount in host.get("Mounts") or []:
        mounts.append({"Type": mount["Type"], "Name": mount["Source"] if mount["Type"] == "volume" else "",
                       "Source": mount["Source"], "Destination": mount["Target"], "RW": not mount.get("ReadOnly")})
        covered.add(mount["Target"])
    for destination in config.get("Volumes") or {}:
        if destination not in covered and destination not in (host.get("Tmpfs") or {}):
            mounts.append({"Type": "volume", "Name": "0" * 64, "Source": "/x", "Destination": destination, "RW": True})
    names = {}
    for name, endpoint in ((old or {}).get("NetworkSettings", {}).get("Networks") or {}).items():
        names[endpoint["NetworkID"]] = name
    networks = {}
    for name, endpoint in (body.get("NetworkingConfig") or {}).get("EndpointsConfig", {}).items():
        network_id = next((i for i, n in names.items() if n == name), "")
        networks[name] = {**endpoint, "NetworkID": network_id}
    for network_id, endpoint in plan.connects:
        networks[names.get(network_id, network_id)] = {**endpoint, "NetworkID": network_id}
    for endpoint in networks.values():
        if not started:
            endpoint["NetworkID"] = ""
        if short_id_alias and endpoint.get("Aliases"):
            endpoint["Aliases"] = [*endpoint["Aliases"], new_id[:12]]
    return {"Id": new_id, "Name": "/" + plan.name, "Image": new_image["Id"], "Config": config, "HostConfig": host,
            "Mounts": mounts, "NetworkSettings": {"Networks": networks},
            "State": {"Status": "running" if started else "created"}}


# ---------------------------------------------------------------------------
# Aufgezeichnete Engines (Golden)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", RECORDED)
def test_golden_body_from_a_recorded_engine(name):
    _, target, image, new_image_id, drivers, api = recorded(name)
    golden = json.loads((FIXTURES / f"{name}.clone.json").read_text(encoding="ascii"))
    plan = clone.build(target, image, new_image_id=new_image_id, api_version=api, network_drivers=drivers)
    assert golden["new_image_id"] == new_image_id
    assert {"name": plan.name, "body": plan.body, "connects": [list(c) for c in plan.connects]} == {
        "name": golden["name"], "body": golden["body"], "connects": golden["connects"]}


def test_one_code_path_for_all_api_versions():
    bodies = {name: json.loads((FIXTURES / f"{name}.clone.json").read_text())["body"] for name in RECORDED[:3]}
    assert bodies["docker29-api141"] == bodies["docker29-api143"] == bodies["docker29-api154"]


@pytest.mark.parametrize("name", RECORDED)
def test_recorded_body_rules(name):
    _, target, image, new_image_id, drivers, api = recorded(name)
    plan = clone.build(target, image, new_image_id=new_image_id, api_version=api, network_drivers=drivers)
    body = plan.body
    assert "Entrypoint" not in body and "Hostname" not in body and "WorkingDir" not in body
    assert not set(OUTPUT_ONLY) & set(body)
    assert not {"MaskedPaths", "ReadonlyPaths", "ContainerIDFile", "AutoRemove"} & set(body["HostConfig"])
    image_env = set(image["Config"]["Env"])
    assert body["Env"] and not image_env & set(body["Env"])
    assert not any(e.startswith(("PATH=", "PYTHON_VERSION=", "NODVARD_DECK_ENV=")) for e in body["Env"])
    assert policy.VERSION_LABEL not in body["Labels"]  # das Label des alten Images bleibt nicht kleben
    assert body["Labels"][clone.COMPOSE_IMAGE_LABEL] == new_image_id
    assert body["Labels"]["com.docker.compose.service"] == "nodvard-deck"
    assert "Volumes" not in body
    assert body["ExposedPorts"] == {"8080/tcp": {}}  # der veroeffentlichte Port bleibt, obwohl das Image ihn erklaert
    assert body["Image"] == target["Config"]["Image"]  # unveraenderter Tag-Text
    endpoints = body["NetworkingConfig"]["EndpointsConfig"]
    assert list(endpoints) == [target["HostConfig"]["NetworkMode"]]  # genau ein Endpunkt, das Primaernetz
    for endpoint in [*endpoints.values(), *(c[1] for c in plan.connects)]:
        assert target["Id"][:12] not in endpoint.get("Aliases", [])
        assert not {"NetworkID", "EndpointID", "Gateway", "IPAddress", "MacAddress", "DNSNames"} & set(endpoint)
    Engine()._check_create_body(body)  # der Engine-Client nimmt den Body an
    json.dumps(body)


def test_recorded_anonymous_data_volume_is_kept_as_mount():
    _, target, image, new_image_id, drivers, api = recorded("docker29-api154")
    anonymous = next(m for m in target["Mounts"] if m["Destination"] == "/app/data")
    assert anonymous["Type"] == "volume" and re.fullmatch(r"[0-9a-f]{64}", anonymous["Name"])
    assert not any("/app/data" in b for b in target["HostConfig"]["Binds"])  # nur in .Mounts sichtbar
    plan = clone.build(target, image, new_image_id=new_image_id, api_version=api, network_drivers=drivers)
    assert {"Type": "volume", "Source": anonymous["Name"], "Target": "/app/data", "ReadOnly": False} in \
        plan.body["HostConfig"]["Mounts"]


def test_recorded_named_volume_needs_no_extra_mount():
    _, target, image, new_image_id, drivers, api = recorded("docker29-api154-stack")
    plan = clone.build(target, image, new_image_id=new_image_id, api_version=api, network_drivers=drivers)
    assert "Mounts" not in plan.body["HostConfig"]
    assert "u2stack_data:/app/data:rw" in plan.body["HostConfig"]["Binds"]
    assert plan.body["Image"] == policy.REPOSITORY  # ohne Tag: gilt als latest, bleibt aber unveraendert


def test_recorded_api143_aliases_contain_the_short_id_but_the_body_does_not():
    _, target, image, new_image_id, drivers, api = recorded("docker29-api143")
    short = target["Id"][:12]
    assert all(short in e["Aliases"] for e in target["NetworkSettings"]["Networks"].values())
    plan = clone.build(target, image, new_image_id=new_image_id, api_version=api, network_drivers=drivers)
    endpoint = plan.body["NetworkingConfig"]["EndpointsConfig"]["u2probe_back"]
    assert endpoint["Aliases"] == ["u2probe-nodvard-deck-1", "nodvard-deck"]
    assert endpoint["IPAMConfig"] == {"IPv4Address": "172.31.251.10"}
    assert plan.connects[0][1]["Aliases"] == ["u2probe-nodvard-deck-1", "nodvard-deck", "deck-front"]


def test_recorded_anonymous_compose_volume_keeps_its_volume():
    # `volumes: [/app/data]` in Compose: HostConfig.Mounts ohne Source (aufgezeichnet mit Compose 5.1.1).
    _, target, image, new_image_id, drivers, api = recorded("docker29-api154-anon")
    assert target["HostConfig"]["Mounts"] == [{"Target": "/app/data", "Type": "volume", "VolumeOptions": {}}]
    data = next(m for m in target["Mounts"] if m["Destination"] == "/app/data")
    plan = clone.build(target, image, new_image_id=new_image_id, api_version=api, network_drivers=drivers)
    mounts = plan.body["HostConfig"]["Mounts"]
    assert {"Type": "volume", "Source": data["Name"], "Target": "/app/data", "VolumeOptions": {}} in mounts
    assert [m["Target"] for m in mounts].count("/app/data") == 1  # nicht noch einmal als anonymes Volume angehaengt


def test_recorded_anonymous_compose_volume_pair():
    # An der echten Engine angelegt und gestartet: /app/data ist dasselbe Volume wie vorher, sonst lehnt die
    # Nachkontrolle ab. Ohne die Ergaenzung der Source (der naive Klon) bekaeme der neue ein frisches Volume.
    data, plan = pair("docker29-api154-anon-pair")
    old_data = clone.mount_map(data["old"])["/app/data"]
    for stage in ("created", "started"):
        clone.verify(data["old"], data[stage], plan, new_image=data["new_image"], new_image_id=data[stage]["Image"])
        assert clone.mount_map(data[stage])["/app/data"] == old_data
        new = copy.deepcopy(data[stage])
        next(m for m in new["Mounts"] if m["Destination"] == "/app/data").update(Name="0" * 64)
        assert refusal(clone.verify, data["old"], new, plan, new_image=data["new_image"],
                       new_image_id=new["Image"]).detail == "mounts"
    assert data["old"]["HostConfig"]["Mounts"][0].get("Source") is None  # so stand es beim alten Container
    assert plan.body["HostConfig"]["Mounts"][0]["Source"] == old_data[1]


def test_recorded_tmpfs_and_extra_hosts_are_kept():
    _, target, image, new_image_id, drivers, api = recorded("docker29-api154")
    plan = clone.build(target, image, new_image_id=new_image_id, api_version=api, network_drivers=drivers)
    assert plan.body["HostConfig"]["Tmpfs"] == {"/tmp/u2": ""}  # leerer Wert, Bedeutung im Schluessel
    assert plan.body["HostConfig"]["ExtraHosts"] == ["u2probe.local:127.0.0.1"]


FORBIDDEN_IN_FIXTURES = {
    "192.168.x.x": re.compile(r"192\.168\."),
    "10.x.x.x": re.compile(r"(?<![0-9.])10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}"),
    "100.64.x.x bis 100.127.x.x": re.compile(
        r"(?<![0-9.])100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}"),
    "/Users/": re.compile(r"/Users/"),
    "/root/": re.compile(r"/root/"),
    "/home/": re.compile(r"/home/"),
}
"""Was in keiner eingecheckten Aufnahme stehen darf: Adressen aus Heimnetzen und Tailscale, Heimatordner."""


def test_fixtures_are_scrubbed():
    token = re.compile(r"scrubbed-[0-9]+")
    keep = ("com.docker.compose.", "org.opencontainers.image.")
    files = sorted(FIXTURES.glob("docker*.json"))
    assert len(files) >= 10
    tool = _record_tool()

    def walk(obj, where):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "Env" and isinstance(value, list):
                    for entry in value:
                        assert token.fullmatch(entry.partition("=")[2]) or entry.partition("=")[2] == "", where
                elif key == "Labels" and isinstance(value, dict) and not where.endswith("filters"):
                    for label, text in value.items():
                        assert label.startswith(keep) or token.fullmatch(text) or text == "", (where, label)
                elif key == "Log" and isinstance(value, list):
                    assert all(e.get("Output", "") == "" for e in value), where
                else:
                    walk(value, f"{where}.{key}")
        elif isinstance(obj, list):
            for item in obj:
                walk(item, where)

    for path in files:
        text = path.read_text(encoding="ascii")
        assert SECRET not in text, path.name
        for what, pattern in FORBIDDEN_IN_FIXTURES.items():
            assert not pattern.search(text), (path.name, what)
        data = json.loads(text)
        assert tool.find_leaks(data) == [], path.name  # dieselbe Suche, mit der das Werkzeug seine Aufnahme prueft
        walk(data, path.name)


# ---------------------------------------------------------------------------
# Echte Paare: Nachkontrolle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", PAIRS)
@pytest.mark.parametrize("stage", ["created", "started"])
def test_recorded_pair_passes_the_check(name, stage):
    data, plan = pair(name)
    if stage == "created":
        assert all(e["NetworkID"] == "" for e in data["created"]["NetworkSettings"]["Networks"].values())
    clone.verify(data["old"], data[stage], plan, new_image=data["new_image"], new_image_id=data[stage]["Image"])
    assert clone.mount_map(data["old"])["/app/data"] == clone.mount_map(data[stage])["/app/data"]


def _mutations():
    yield "Image", lambda c: c.update(Image="sha256:" + "9" * 64)
    yield "Name", lambda c: c.update(Name="/other")
    yield "Mounts", lambda c: next(m for m in c["Mounts"] if m["Destination"] == "/app/data").update(Name="0" * 64)
    yield "Mounts", lambda c: c["Mounts"].pop()
    yield "Networks", lambda c: c["NetworkSettings"]["Networks"]["u2probe_back"].update(
        IPAMConfig={"IPv4Address": "172.31.251.99"})
    yield "Networks", lambda c: c["NetworkSettings"]["Networks"]["u2probe_front"].update(Aliases=["x"])
    yield "Networks", lambda c: c["NetworkSettings"]["Networks"].pop("u2probe_front")
    yield "PortBindings", lambda c: c["HostConfig"].update(PortBindings={})
    yield "MaskedPaths", lambda c: c["HostConfig"].update(MaskedPaths=[])
    yield "ReadonlyPaths", lambda c: c["HostConfig"].update(ReadonlyPaths=[])
    yield "MaskedPaths", lambda c: c["HostConfig"].update(MaskedPaths=c["HostConfig"]["MaskedPaths"][1:])
    yield "RestartPolicy", lambda c: c["HostConfig"].update(RestartPolicy={"Name": "no", "MaximumRetryCount": 0})
    yield "Tmpfs", lambda c: c["HostConfig"].pop("Tmpfs")
    yield "Privileged", lambda c: c["HostConfig"].update(Privileged=True)
    yield "AutoRemove", lambda c: c["HostConfig"].update(AutoRemove=True)
    yield "SomethingNew", lambda c: c["HostConfig"].update(SomethingNew=1)
    yield "Env", lambda c: c["Config"].update(Env=[e for e in c["Config"]["Env"] if not e.startswith("U2_SECRET=")])
    yield "Env", lambda c: c["Config"]["Env"].append("EXTRA=1")
    yield "Labels", lambda c: c["Config"]["Labels"].pop("u2probe.user")
    yield "Labels", lambda c: c["Config"]["Labels"].update({clone.COMPOSE_IMAGE_LABEL: "sha256:" + "8" * 64})
    yield "Entrypoint", lambda c: c["Config"].update(Entrypoint=["uvicorn"])
    yield "Cmd", lambda c: c["Config"].update(Cmd=["sh"])
    yield "User", lambda c: c["Config"].update(User="root")
    yield "WorkingDir", lambda c: c["Config"].update(WorkingDir="/")
    yield "Healthcheck", lambda c: c["Config"]["Healthcheck"].update(Test=["NONE"])
    yield "Healthcheck", lambda c: c["Config"]["Healthcheck"].update(Retries=99)
    yield "ExposedPorts", lambda c: c["Config"].update(ExposedPorts={"9999/tcp": {}})
    yield "Volumes", lambda c: c["Config"].update(Volumes=None)
    yield "Tty", lambda c: c["Config"].update(Tty=True)
    yield "StopTimeout", lambda c: c["Config"].update(StopTimeout=1)
    yield "Image", lambda c: c["Config"].update(Image=policy.REPOSITORY + ":0.7.1")
    yield "NetworkDisabled", lambda c: c["Config"].update(NetworkDisabled=True)
    yield "Brand_new", lambda c: c["Config"].update(Brand_new="x")


@pytest.mark.parametrize("name", PAIRS)
@pytest.mark.parametrize(("field", "mutate"), list(_mutations()), ids=[m[0] for m in _mutations()])
def test_recorded_pair_rejects_every_unexplained_difference(name, field, mutate):
    data, plan = pair(name)
    for stage in ("created", "started"):
        new = copy.deepcopy(data[stage])
        mutate(new)
        exc = refusal(clone.verify, data["old"], new, plan, new_image=data["new_image"],
                      new_image_id=data[stage]["Image"])
        assert exc.code == policy.CLONE_MISMATCH
        assert exc.detail == clone._detail(field), (stage, exc.detail)


def test_network_id_must_match_once_both_sides_report_one():
    data, plan = pair("docker29-api154-pair")
    new = copy.deepcopy(data["started"])
    new["NetworkSettings"]["Networks"]["u2probe_front"]["NetworkID"] = "9" * 64
    assert refusal(clone.verify, data["old"], new, plan, new_image=data["new_image"],
                   new_image_id=new["Image"]).detail == "networks"


def test_recorded_pair_new_image_values_apply():
    data, _ = pair("docker29-api154-pair")
    started = data["started"]["Config"]
    new_image = data["new_image"]["Config"]
    assert started["Labels"][policy.VERSION_LABEL] == "0.7.1"
    assert set(new_image["Env"]) - {e for e in new_image["Env"] if _key(e) in {_key(x) for x in data["old"]["Config"][
        "Env"] if x not in data["old_image"]["Config"]["Env"]}} <= set(started["Env"])
    assert started["Healthcheck"]["Interval"] == new_image["Healthcheck"]["Interval"]  # herausgerechnet
    assert started["Healthcheck"]["Test"][0] == "CMD"  # der Test aus Compose bleibt


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_entrypoint_is_never_sent():
    plan = build(container(**{"Config.Entrypoint": ["/entrypoint.sh"]}), image_config(Entrypoint=["/entrypoint.sh"]))
    assert "Entrypoint" not in plan.body


@pytest.mark.parametrize(("entrypoint", "image_entrypoint"), [
    ([], ["/entrypoint.sh"]),          # `entrypoint: []` schaltet boot und die Vorher-Kopie ab
    ([""], ["/entrypoint.sh"]),
    (["/bin/sh"], ["/entrypoint.sh"]),
    (["/entrypoint.sh", "x"], ["/entrypoint.sh"]),
    (["/entrypoint.sh"], None),
    (None, None),                      # leer bleibt leer, auch wenn das Image keinen hat (Ablehnung)
    ([], None),
    (None, []),
    ([], []),
])
def test_custom_entrypoint_is_refused(entrypoint, image_entrypoint):
    exc = refusal(build, container(**{"Config.Entrypoint": entrypoint}), image_config(Entrypoint=image_entrypoint))
    assert exc.code == policy.CUSTOM_ENTRYPOINT


def test_empty_cmd_next_to_an_image_cmd_cannot_be_rebuilt():
    assert refusal(build, container(**{"Config.Cmd": []})).code == policy.CUSTOM_ENTRYPOINT
    assert refusal(build, container(**{"Config.Cmd": None})).code == policy.CUSTOM_ENTRYPOINT


def test_cmd_only_when_different_from_the_image():
    assert "Cmd" not in build().body
    assert build(container(**{"Config.Cmd": ["uvicorn", "x", "--workers", "2"]})).body["Cmd"] == [
        "uvicorn", "x", "--workers", "2"]
    assert "Cmd" not in build(container(**{"Config.Cmd": None}), image_config(Cmd=None)).body


@pytest.mark.parametrize(("bad", "field"), [
    ({"Config.Entrypoint": "/entrypoint.sh"}, "entrypoint"), ({"Config.Cmd": "uvicorn"}, "cmd"),
    ({"Config.Env": "A=1"}, "env"), ({"Config.Env": [1]}, "env"), ({"Config.Labels": []}, "labels"),
    ({"Config.Labels": {"a": 1}}, "labels"), ({"Config.User": 0}, "user"), ({"Config.Hostname": 5}, "hostname"),
    ({"Config.ExposedPorts": []}, "exposed_ports"), ({"Config.Image": ""}, "image"), ({"Config.Image": None}, "image"),
    ({"Config": []}, "config"), ({"HostConfig": "x"}, "host_config"), ({"Id": "short"}, "id"), ({"Name": "x"}, "name"),
])
def test_wrong_types_are_unknown_fields(bad, field):
    exc = refusal(build, container(**bad))
    assert exc.code == policy.UNKNOWN_FIELD and exc.detail == field


def test_env_without_the_image_entries():
    body = build(container(**{"Config.Env": ["NODVARD_DECK_AUDIT_RETENTION_DAYS=90", "PATH=/usr/local/bin:/usr/bin",
                                             "NODVARD_DECK_ENV=dev", "PYTHON_VERSION=3.12.14",
                                             "NODVARD_DECK_JWT_SECRET=x"]})).body
    # Nutzerwerte bleiben (auch ein vom Image abweichender Wert), exakt gleiche Image-Eintraege fallen weg.
    assert body["Env"] == ["NODVARD_DECK_AUDIT_RETENTION_DAYS=90", "NODVARD_DECK_ENV=dev", "NODVARD_DECK_JWT_SECRET=x"]


def test_plan_repr_does_not_reveal_environment_values():
    # Der Body enthaelt die Umgebungswerte im Klartext. Landet ein `Plan` einmal in einer Fehlermeldung oder in einer
    # Protokollzeile (`repr`, `str`, f-String, `%r`), duerfen die Geheimnisse nicht mitkommen.
    inspect = container(**{"Config.Env": ["NODVARD_DECK_JWT_SECRET=" + SECRET]})
    inspect["NetworkSettings"]["Networks"]["deck_front"]["DriverOpts"] = {"token": SECRET + "-opt"}
    plan = build(inspect)
    assert plan.connects and SECRET in json.dumps(plan.body) and SECRET in json.dumps(plan.connects)  # sie sind drin
    for text in (repr(plan), str(plan), f"{plan}", f"{plan!r}", repr([plan]), repr({"plan": plan})):
        assert SECRET not in text
        assert plan.name in text  # der Name genuegt zum Zuordnen in einem Protokoll


def test_env_value_equal_to_the_old_default_follows_the_new_image():
    # Bekannte, dokumentierte Folge des Herausrechnens: der Nutzer setzt NODVARD_DECK_ENV=prod (= alter Standard).
    body = build(container(**{"Config.Env": ["NODVARD_DECK_ENV=prod"]})).body
    assert "Env" not in body


def test_labels():
    inspect = container()
    inspect["Config"]["Labels"]["org.opencontainers.image.source"] = "other"  # Nutzer hat ueberschrieben
    inspect["Config"]["Labels"]["com.docker.compose.extra"] = "x"
    image = image_config(Labels={policy.VERSION_LABEL: "0.7.0", "org.opencontainers.image.source": "x",
                                 "com.docker.compose.extra": "x"})
    labels = build(inspect, image).body["Labels"]
    assert policy.VERSION_LABEL not in labels                    # gleich im Image: faellt weg
    assert labels["org.opencontainers.image.source"] == "other"  # abweichend: bleibt
    assert labels["com.docker.compose.extra"] == "x"             # Compose-Labels bleiben immer
    assert labels[clone.COMPOSE_IMAGE_LABEL] == NEW_IMAGE        # = neue Image-ID
    assert labels["user.label"] == "mine"


def test_compose_image_label_only_when_present():
    inspect = container()
    del inspect["Config"]["Labels"][clone.COMPOSE_IMAGE_LABEL]
    assert clone.COMPOSE_IMAGE_LABEL not in build(inspect).body["Labels"]


@pytest.mark.parametrize(("changes", "kept"), [
    ({}, False),                                                  # Kurz-ID: weglassen
    ({"Config.Hostname": "deck"}, True),                          # eigener Name: uebernehmen
    ({"Config.Hostname": "deck", "HostConfig.NetworkMode": "host"}, False),
    ({"Config.Hostname": "deck", "HostConfig.NetworkMode": "container:" + "7" * 64}, False),
    ({"Config.Hostname": "deck", "HostConfig.UTSMode": "host"}, False),
    ({"Config.Hostname": "deck", "HostConfig.NetworkMode": "none"}, True),
    ({"Config.Hostname": ""}, False),
])
def test_hostname(changes, kept):
    body = build(container(**changes)).body
    assert ("Hostname" in body) is kept
    if kept:
        assert body["Hostname"] == "deck"


def test_user_workdir_stopsignal_are_subtracted():
    image = image_config(User="1000", WorkingDir="/app", StopSignal="SIGTERM")
    same = container(**{"Config.User": "1000", "Config.WorkingDir": "/app", "Config.StopSignal": "SIGTERM"})
    assert not {"User", "WorkingDir", "StopSignal"} & set(build(same, image).body)
    other = container(**{"Config.User": "0", "Config.WorkingDir": "/srv", "Config.StopSignal": "SIGINT"})
    body = build(other, image).body
    assert (body["User"], body["WorkingDir"], body["StopSignal"]) == ("0", "/srv", "SIGINT")


def test_copied_fields():
    inspect = container(**{"Config.Domainname": "lan", "Config.Tty": True, "Config.OpenStdin": True,
                           "Config.StdinOnce": True, "Config.AttachStdin": True, "Config.StopTimeout": 45})
    body = build(inspect).body
    assert (body["Domainname"], body["Tty"], body["OpenStdin"], body["StdinOnce"], body["AttachStdin"],
            body["StopTimeout"]) == ("lan", True, True, True, True, 45)
    assert "Tty" not in build().body  # Standardwert: weglassen


def test_exposed_ports_and_volumes_without_the_image_keys():
    # Ohne veroeffentlichten Port gilt die Regel der Config: Schluessel des alten Images fallen heraus.
    inspect = container(**{"Config.ExposedPorts": {"8080/tcp": {}, "9000/udp": {}},
                           "Config.Volumes": {"/app/data": {}, "/extra": {}}, "HostConfig.PortBindings": None})
    body = build(inspect).body
    assert body["ExposedPorts"] == {"9000/udp": {}} and body["Volumes"] == {"/extra": {}}
    assert "ExposedPorts" not in build(container(**{"HostConfig.PortBindings": {}})).body


def test_published_ports_stay_in_exposed_ports_even_when_the_old_image_exposes_them():
    # Compose schreibt jeden Port aus `ports:` auch in `ExposedPorts`; der Docker-Dienst (vor 29) veroeffentlicht
    # einen Eintrag aus `PortBindings` nur, wenn der Port dort steht. Wuerde der Klon ihn "herausrechnen", haengt der
    # Zugang von `EXPOSE` im NEUEN Image ab.
    inspect = container(**{"Config.ExposedPorts": {"8080/tcp": {}, "9000/udp": {}, "7000/tcp": {}},
                           "HostConfig.PortBindings": {"8080/tcp": [{"HostIp": "", "HostPort": "8080"}],
                                                       "9000/udp": [{"HostIp": "", "HostPort": "9000"}]}})
    image = image_config(ExposedPorts={"8080/tcp": {}, "7000/tcp": {}})
    body = build(inspect, image).body
    # 8080 (veroeffentlicht, im Image) bleibt, 9000 (nicht im Image) bleibt, 7000 (nur im Image, nicht veroeffentlicht)
    # faellt wie bisher heraus.
    assert body["ExposedPorts"] == {"8080/tcp": {}, "9000/udp": {}}
    assert body["HostConfig"]["PortBindings"] == inspect["HostConfig"]["PortBindings"]
    Engine()._check_create_body(body)


def test_new_image_without_expose_keeps_the_published_port():
    old = container()
    image = new_image(ExposedPorts=None)  # eine spaetere Version erklaert den Port nicht mehr
    plan = build(old)
    new = simulate_create(plan, image, old=old)
    assert "8080/tcp" in new["Config"]["ExposedPorts"]
    clone.verify(old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    # Der naive Klon (ohne den Port im Body) verliert ihn: das Ergebnis passt zum erwarteten `Image + Body`, nur die
    # Nachkontrolle auf veroeffentlichte Ports merkt es.
    naive = clone.Plan(name=plan.name, body={k: v for k, v in plan.body.items() if k != "ExposedPorts"},
                       connects=plan.connects)
    lost = simulate_create(naive, image, old=old)
    assert not lost["Config"]["ExposedPorts"]
    exc = refusal(clone.verify, old, lost, naive, new_image=image, new_image_id=NEW_IMAGE)
    assert (exc.code, exc.detail) == (policy.CLONE_MISMATCH, "exposed_ports")


def test_verify_wants_published_ports_to_stay_exposed():
    # Nur diese Regel darf hier greifen: Body und neues Image erklaeren den Port beide nicht, das Ergebnis passt also
    # genau zu `Image + Body` (die Gleichheitspruefung in `_verify_config` ist zufrieden). Allein die Regel fuer
    # veroeffentlichte Ports sieht, dass der alte Container 8080 veroeffentlicht hat und der neue ihn nicht erklaert.
    old = container()
    image = new_image(ExposedPorts=None)
    plan = build(old)
    plan = clone.Plan(name=plan.name, body={k: v for k, v in plan.body.items() if k != "ExposedPorts"},
                      connects=plan.connects)
    new = simulate_create(plan, image, old=old)
    assert not new["Config"]["ExposedPorts"]
    clone._verify_config(old["Config"], new["Config"], plan.body, image["Config"], lambda field: AssertionError(field))
    exc = refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    assert (exc.code, exc.detail) == (policy.CLONE_MISMATCH, "exposed_ports")


def test_verify_refuses_a_foreign_exposed_port_by_equality():
    # Die andere Seite: ein Port, der weder im Image noch im Body steht, faellt in der Gleichheitspruefung auf.
    old = container()
    plan = build(old)
    image = new_image()
    new = simulate_create(plan, image, old=old)
    new["Config"]["ExposedPorts"] = {"8080/tcp": {}, "9999/tcp": {}}
    assert refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE).detail == "exposed_ports"


def test_a_port_the_old_container_never_exposed_is_no_reason_to_refuse():
    # PortBindings ohne ExposedPorts beim alten (so veroeffentlichte schon der alte nichts): kein Fehlalarm.
    old = container(**{"Config.ExposedPorts": None, "HostConfig.PortBindings": {"8080/tcp": [{"HostPort": "8080"}]}})
    image = new_image(ExposedPorts=None)
    plan = build(old, image_config(ExposedPorts=None))
    assert "ExposedPorts" not in plan.body
    clone.verify(old, simulate_create(plan, image, old=old), plan, new_image=image, new_image_id=NEW_IMAGE)


def test_healthcheck_per_field():
    body = build().body
    assert body["Healthcheck"] == {"Test": ["CMD", "python", "-c", "x"]}  # Zeiten gleich dem Image: weggelassen
    same = container(**{"Config.Healthcheck": image_config()["Config"]["Healthcheck"]})
    assert "Healthcheck" not in build(same).body
    own = container(**{"Config.Healthcheck": {**image_config()["Config"]["Healthcheck"], "Interval": 5_000_000_000,
                                              "StartInterval": 1_000_000_000}})
    assert build(own).body["Healthcheck"] == {"Interval": 5_000_000_000, "StartInterval": 1_000_000_000}
    without_image = build(container(), image_config(Healthcheck=None)).body["Healthcheck"]
    assert without_image["Interval"] == 30_000_000_000 and without_image["Test"][0] == "CMD"
    assert refusal(build, container(**{"Config.Healthcheck.Future": 1})).code == policy.UNKNOWN_FIELD
    assert "Future" not in build(container(**{"Config.Healthcheck.Future": 0})).body.get("Healthcheck", {})


def test_dropped_config_fields():
    inspect = container(**{"Config.MacAddress": "02:42:ac:11:00:02", "Config.OnBuild": ["RUN x"],
                           "Config.ArgsEscaped": True, "Config.Shell": ["/bin/sh", "-c"],
                           "Config.NetworkDisabled": False})
    assert not {"MacAddress", "OnBuild", "ArgsEscaped", "Shell", "NetworkDisabled"} & set(build(inspect).body)


def test_network_disabled_is_an_unknown_field():
    exc = refusal(build, container(**{"Config.NetworkDisabled": True}))
    assert (exc.code, exc.detail) == (policy.UNKNOWN_FIELD, "network_disabled")


@pytest.mark.parametrize("value", [None, False, 0, "", [], {}])
def test_unknown_config_field_with_default_value_is_ignored(value):
    assert "BrandNew" not in build(container(**{"Config.BrandNew": value})).body


@pytest.mark.parametrize("value", [True, 1, "x", ["x"], {"a": None}, {"": ""}])
def test_unknown_config_field_with_a_value_is_refused(value):
    exc = refusal(build, container(**{"Config.BrandNew": value}))
    assert (exc.code, exc.detail) == (policy.UNKNOWN_FIELD, "brand_new")


def test_image_config_missing():
    assert refusal(build, container(), {"Id": OLD_IMAGE}).code == policy.IMAGE_CONFIG_MISSING
    assert refusal(build, container(), {"Id": OLD_IMAGE, "Config": None}).code == policy.IMAGE_CONFIG_MISSING
    assert refusal(clone.build, container(), [], new_image_id=NEW_IMAGE, api_version=(1, 54),
                   network_drivers=DRIVERS).code == policy.IMAGE_CONFIG_MISSING


def test_new_image_id_must_be_an_id():
    with pytest.raises(ValueError):
        build(new_image_id="ghcr.io/nodvard/deck:latest")


# ---------------------------------------------------------------------------
# HostConfig
# ---------------------------------------------------------------------------


def test_hostconfig_fields_are_copied_unchanged():
    values = {field: f"value-{field}" for field in clone.HOST_COPY}
    values["Mounts"] = [{"Type": "bind", "Source": "/srv", "Target": "/srv"}]
    values["Binds"] = ["/x:/y"]
    values["Tmpfs"] = {"/run": "size=1m"}
    values["NetworkMode"] = "host"
    inspect = container(Mounts=[])
    inspect["HostConfig"] = {**values, "AutoRemove": False}
    body = build(inspect).body
    assert body["HostConfig"] == values
    Engine()._check_create_body(body)  # jedes Feld, das `build` aus HOST_COPY uebernimmt, darf zum Engine-Client


def test_hostconfig_drop_list():
    inspect = container(**{"HostConfig.MaskedPaths": ["/proc/x"], "HostConfig.ReadonlyPaths": ["/proc/y"],
                           "HostConfig.KernelMemory": 1024, "HostConfig.KernelMemoryTCP": 2048,
                           "HostConfig.ContainerIDFile": "/tmp/id"})
    host = build(inspect).body["HostConfig"]
    assert not set(clone.HOST_DROP) & set(host)
    assert not set(clone.HOST_PATHS) & set(host)  # eine Liste fehlt im Body: es gilt die Standardliste des Daemons


@pytest.mark.parametrize("field", clone.HOST_PATHS)
def test_explicitly_empty_system_paths_are_sent_as_empty(field):
    # `systempaths=unconfined` kommt im Inspect als `[]` an. Ohne dieses `[]` im Body setzte der Daemon die
    # Standardliste: der Klon waere stillschweigend strenger (anders als der alte).
    plan = build(container(**{f"HostConfig.{field}": []}))
    assert plan.body["HostConfig"][field] == []
    for other in set(clone.HOST_PATHS) - {field}:
        assert other not in plan.body["HostConfig"]  # die andere Liste war nicht leer
    Engine()._check_create_body(plan.body)


@pytest.mark.parametrize("value", [None, ["/proc/x"]])
def test_other_system_paths_are_left_to_the_daemon(value):
    host = build(container(**{"HostConfig.MaskedPaths": value})).body["HostConfig"]
    assert "MaskedPaths" not in host


def test_privileged_container_never_sends_system_paths():
    inspect = container(**{"HostConfig.Privileged": True, "HostConfig.MaskedPaths": [],
                           "HostConfig.ReadonlyPaths": []})
    assert not set(clone.HOST_PATHS) & set(build(inspect).body["HostConfig"])


@pytest.mark.parametrize("field", clone.HOST_PATHS)
def test_verify_wants_at_least_the_paths_of_the_old_container(field):
    old = container(**{f"HostConfig.{field}": ["/proc/kcore", "/proc/extra"]})
    plan = build(old)
    image = new_image()
    new = simulate_create(plan, image, old=old)  # der Daemon setzt seine Standardliste, ohne `/proc/extra`
    exc = refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    assert (exc.code, exc.detail) == (policy.CLONE_MISMATCH, clone._detail(field))
    new["HostConfig"][field] = ["/proc/extra", "/proc/kcore", "/sys/devices/virtual/powercap"]  # laenger ist in Ordnung
    clone.verify(old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    for bad in ([], None, "x", [1]):
        new["HostConfig"][field] = bad
        assert refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE).detail == \
            clone._detail(field)


BROKEN_PATH_VALUES = ("x", [1], {"a": 1}, 0, ["/proc/kcore", None])


@pytest.mark.parametrize("field", clone.HOST_PATHS)
def test_verify_keeps_unconfined_unconfined(field):
    old = container(**{f"HostConfig.{field}": []})
    plan = build(old)
    image = new_image()
    new = simulate_create(plan, image, old=old)
    assert new["HostConfig"][field] == []
    clone.verify(old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    new["HostConfig"][field] = ["/proc/kcore"]  # der Klon waere stillschweigend strenger
    assert refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE).detail == \
        clone._detail(field)
    # `null` hiesse beim neuen Container die Standardliste des Daemons: auch das ist strenger als `[]`.
    new["HostConfig"][field] = None
    assert refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE).detail == \
        clone._detail(field)


@pytest.mark.parametrize("field", clone.HOST_PATHS)
@pytest.mark.parametrize("old_value", [[], None, ["/proc/kcore"]])
def test_verify_refuses_broken_path_values(field, old_value):
    # Auch gegen einen alten Wert, bei dem die Teilmengen-Pruefung allein nichts abfaengt (`[]`, `null`).
    old = container(**{f"HostConfig.{field}": old_value})
    plan = build(old)
    image = new_image()
    new = simulate_create(plan, image, old=old)
    for bad in BROKEN_PATH_VALUES:
        new["HostConfig"][field] = bad
        assert refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE).detail == \
            clone._detail(field)


@pytest.mark.parametrize("field", clone.HOST_PATHS)
def test_null_paths_mean_the_default_list_not_no_masks(field):
    # `null` im Inspect des alten Containers: der Daemon setzt zur Laufzeit seine Standardliste.
    old = container(**{f"HostConfig.{field}": None})
    plan = build(old)
    assert field not in plan.body["HostConfig"]
    image = new_image()
    new = simulate_create(plan, image, old=old)
    assert new["HostConfig"][field]  # die Standardliste des Daemons
    clone.verify(old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    new["HostConfig"][field] = None  # auch `null` beim neuen heisst Standardliste
    clone.verify(old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    new["HostConfig"][field] = ["/proc/kcore", "/proc/extra"]
    clone.verify(old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    new["HostConfig"][field] = []  # ein Klon ohne Masken waere stillschweigend lockerer als der alte
    exc = refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    assert (exc.code, exc.detail) == (policy.CLONE_MISMATCH, clone._detail(field))


def test_verify_ignores_system_paths_of_privileged_containers():
    # Ein privilegierter Container hat der Daemon nie maskiert; `build` schickt die Listen nicht mit, und der neue
    # Container hat sie auch nicht. Ohne die Ausnahme in `verify` wuerde `/proc/extra` fehlen und der Klon abgelehnt.
    privileged = {"HostConfig.Privileged": True, "HostConfig.MaskedPaths": ["/proc/kcore", "/proc/extra"],
                  "HostConfig.ReadonlyPaths": ["/proc/sys"]}
    old = container(**privileged)
    plan = build(old)
    assert not set(clone.HOST_PATHS) & set(plan.body["HostConfig"])
    image = new_image()
    new = simulate_create(plan, image, old=old)
    assert all(new["HostConfig"].get(field) is None for field in clone.HOST_PATHS)
    clone.verify(old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    new["HostConfig"]["MaskedPaths"] = []
    clone.verify(old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    # Gegenprobe: ohne `Privileged` fehlt der neuen Liste `/proc/extra`, das lehnt `verify` ab.
    plain = {key: value for key, value in privileged.items() if key != "HostConfig.Privileged"}
    old = container(**plain)
    plan = build(old)
    new = simulate_create(plan, image, old=old)
    assert refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE).detail == "masked_paths"


def test_auto_remove_is_refused():
    assert refusal(build, container(**{"HostConfig.AutoRemove": True})).code == policy.AUTO_REMOVE


def test_links_are_rewritten():
    inspect = container(**{"HostConfig.Links": ["/db:/deck-nodvard-deck-1/database", "/cache:/deck-nodvard-deck-1/c"]})
    assert build(inspect).body["HostConfig"]["Links"] == ["db:database", "cache:c"]


@pytest.mark.parametrize("links", [["db:database"], ["/db"], ["/:/x/y"], ["/a/b:/x/y"], [1], "x"])
def test_odd_links_are_refused(links):
    exc = refusal(build, container(**{"HostConfig.Links": links}))
    assert exc.code == policy.UNKNOWN_FIELD


@pytest.mark.parametrize("value", [True, 1, "x", ["x"], {"a": 1}])
def test_unknown_hostconfig_field_is_refused(value):
    exc = refusal(build, container(**{"HostConfig.Futuristic": value}))
    assert (exc.code, exc.detail) == (policy.UNKNOWN_FIELD, "futuristic")


@pytest.mark.parametrize("value", [None, False, 0, "", [], {}])
def test_unknown_hostconfig_field_with_default_is_ignored(value):
    assert "Futuristic" not in build(container(**{"HostConfig.Futuristic": value})).body["HostConfig"]


def test_anonymous_volume_becomes_a_mount():
    host = build().body["HostConfig"]
    assert host["Mounts"] == [{"Type": "volume", "Source": "9" * 64, "Target": "/app/data", "ReadOnly": False}]
    assert host["Binds"] == ["deck_updater:/app/updater:rw"]


@pytest.mark.parametrize("cover", [
    {"HostConfig.Binds": ["deck_updater:/app/updater:rw", "deck_data:/app/data"]},
    {"HostConfig.Binds": ["deck_updater:/app/updater:rw", "/srv/data:/app/data:ro"]},
    {"HostConfig.Binds": ["deck_updater:/app/updater:rw", "C:\\Users\\x\\data:/app/data:rw"]},
    {"HostConfig.Binds": ["deck_updater:/app/updater:rw", "deck_data:/app/data/"]},
    {"HostConfig.Mounts": [{"Type": "volume", "Source": "deck_data", "Target": "/app/data"}]},
    {"HostConfig.Tmpfs": {"/app/data": ""}},
])
def test_covered_volume_is_not_added_again(cover):
    assert "Mounts" not in build(container(**cover)).body["HostConfig"] or all(
        m.get("Source") != "9" * 64 for m in build(container(**cover)).body["HostConfig"]["Mounts"])


def test_single_path_bind_is_an_anonymous_volume_and_refused():
    # Live geprueft (Docker 29.3): `Binds: ["/pfad"]` legt ein anonymes Volume an. Unveraendert gesendet bekaeme der
    # Klon ein neues, leeres -- das soll schon die Vorpruefung melden, nicht erst die Nachkontrolle.
    inspect = container(**{"HostConfig.Binds": ["deck_updater:/app/updater:rw", "/app/data"]})
    exc = refusal(build, inspect)
    assert (exc.code, exc.detail) == (policy.UNKNOWN_FIELD, "binds")


def test_anonymous_compose_volume_in_hostconfig_mounts_keeps_its_volume():
    # Compose legt `volumes: [/app/data]` als HostConfig.Mounts-Eintrag ohne Source an (live: Compose 5.1.1).
    entry = {"Type": "volume", "Target": "/app/data", "VolumeOptions": {"Subpath": "sub"}}
    host = build(container(**{"HostConfig.Mounts": [entry]})).body["HostConfig"]
    assert host["Mounts"] == [{**entry, "Source": "9" * 64}]  # dasselbe Volume, Optionen bleiben
    assert entry == {"Type": "volume", "Target": "/app/data", "VolumeOptions": {"Subpath": "sub"}}  # Eingabe bleibt


@pytest.mark.parametrize("entry", [
    {"Type": "volume", "Target": "/nowhere"},          # kein Volume des alten Containers an diesem Ziel
    {"Type": "volume", "Source": "", "Target": "/nowhere"},
    {"Type": "volume", "Source": None, "Target": None},
])
def test_anonymous_hostconfig_mount_without_old_volume_is_refused(entry):
    exc = refusal(build, container(**{"HostConfig.Mounts": [entry]}))
    assert (exc.code, exc.detail) == (policy.UNKNOWN_FIELD, "mounts")


def test_read_only_anonymous_volume():
    inspect = container()
    inspect["Mounts"][1]["RW"] = False
    assert build(inspect).body["HostConfig"]["Mounts"][0]["ReadOnly"] is True


def test_existing_mounts_and_anonymous_volume_together():
    mount = {"Type": "bind", "Source": "/srv/cfg", "Target": "/cfg", "ReadOnly": True}
    host = build(container(**{"HostConfig.Mounts": [mount]})).body["HostConfig"]
    assert host["Mounts"] == [mount, {"Type": "volume", "Source": "9" * 64, "Target": "/app/data",
                                      "ReadOnly": False}]


def test_volumes_from_with_anonymous_volumes_is_refused():
    exc = refusal(build, container(**{"HostConfig.VolumesFrom": ["other"]}))
    assert (exc.code, exc.detail) == (policy.UNKNOWN_FIELD, "volumes_from")


@pytest.mark.parametrize("mounts", [
    [{"Type": "volume", "Name": "", "Destination": "/x", "RW": True}],
    [{"Type": "volume", "Name": "v", "Destination": "/x", "RW": "yes"}],
    [{"Type": "volume", "Name": "v", "RW": True}],
    ["x"], "x",
])
def test_odd_mount_entries_are_refused(mounts):
    assert refusal(build, container(Mounts=mounts)).code == policy.UNKNOWN_FIELD


def test_bind_without_destination_is_refused():
    assert refusal(build, container(**{"HostConfig.Binds": ["relative"]})).code == policy.UNKNOWN_FIELD


def test_bind_destination():
    assert clone.bind_destination("vol:/app/data:rw") == "/app/data"
    assert clone.bind_destination("/host:/app/data") == "/app/data"
    assert clone.bind_destination("C:\\x:/app/data:ro") == "/app/data"
    assert clone.bind_destination("/app/data") == "/app/data"
    assert clone.bind_destination("x") is None


# ---------------------------------------------------------------------------
# Netze
# ---------------------------------------------------------------------------


def test_primary_network_in_create_others_per_connect():
    plan = build()
    assert plan.body["NetworkingConfig"] == {"EndpointsConfig": {"deck_back": {
        "IPAMConfig": {"IPv4Address": "172.30.0.10"}, "Aliases": ["deck-nodvard-deck-1", "nodvard-deck"]}}}
    assert plan.connects == ((NET_A, {"Aliases": ["deck-nodvard-deck-1", "nodvard-deck", "deck-front"]}),)


def test_same_body_for_api_143_and_154():
    assert build(api=(1, 43)).body == build(api=(1, 54)).body


@pytest.mark.parametrize("mode", ["host", "none", "container:" + "7" * 64])
def test_no_networking_config_for_shared_or_no_network(mode):
    plan = build(container(**{"HostConfig.NetworkMode": mode}), drivers={})
    assert "NetworkingConfig" not in plan.body and plan.connects == ()
    assert plan.body["HostConfig"]["NetworkMode"] == mode


@pytest.mark.parametrize("mode", ["default", "bridge", ""])
def test_default_bridge_without_aliases(mode):
    inspect = container(**{"HostConfig.NetworkMode": mode})
    inspect["NetworkSettings"]["Networks"] = {"bridge": {"Aliases": ["x"], "NetworkID": NET_BRIDGE,
                                                         "IPAMConfig": None}}
    plan = build(inspect)
    assert plan.body["NetworkingConfig"] == {"EndpointsConfig": {"bridge": {}}}


def test_primary_network_by_id():
    inspect = container(**{"HostConfig.NetworkMode": NET_B})
    plan = build(inspect)
    assert list(plan.body["NetworkingConfig"]["EndpointsConfig"]) == [NET_B]
    assert [c[0] for c in plan.connects] == [NET_A]


@pytest.mark.parametrize("changes", [
    {"HostConfig.NetworkMode": "elsewhere"},
    {"NetworkSettings.Networks": {}},
    {"NetworkSettings.Networks": None},
    {"NetworkSettings": None},
    {"NetworkSettings.Networks.deck_back.NetworkID": ""},
    {"NetworkSettings.Networks.deck_back.NetworkID": "short"},
    {"NetworkSettings.Networks.deck_front.NetworkID": NET_B},  # zwei Treffer fuer das Primaernetz
    {"NetworkSettings.Networks.deck_front": "x"},
])
def test_unclear_networks_are_refused(changes):
    assert refusal(build, container(**changes)).code == policy.NETWORK_UNCLEAR


def test_network_without_known_driver_is_unclear():
    assert refusal(build, container(), drivers={NET_A: "bridge"}).code == policy.NETWORK_UNCLEAR


@pytest.mark.parametrize("driver", ["macvlan", "ipvlan"])
def test_macvlan_and_ipvlan_are_refused(driver):
    assert refusal(build, container(), drivers={NET_A: "bridge", NET_B: driver}).code == policy.MACVLAN


def test_gw_priority_from_api_148():
    inspect = container(**{"NetworkSettings.Networks.deck_front.GwPriority": 5})
    assert build(inspect, api=(1, 48)).connects[0][1]["GwPriority"] == 5
    assert refusal(build, inspect, api=(1, 47)).code == policy.UNKNOWN_FIELD
    zero = container(**{"NetworkSettings.Networks.deck_front.GwPriority": 0})
    assert "GwPriority" not in build(zero, api=(1, 43)).connects[0][1]


def test_endpoint_copies_links_and_driver_opts_and_cleans_ipam():
    inspect = container(**{"NetworkSettings.Networks.deck_front.Links": ["db:database"],
                           "NetworkSettings.Networks.deck_front.DriverOpts": {"com.example": "1"},
                           "NetworkSettings.Networks.deck_front.IPAMConfig": {"IPv4Address": "", "IPv6Address": "fd::5",
                                                                              "LinkLocalIPs": None}})
    endpoint = build(inspect).connects[0][1]
    assert endpoint["Links"] == ["db:database"] and endpoint["DriverOpts"] == {"com.example": "1"}
    assert endpoint["IPAMConfig"] == {"IPv6Address": "fd::5"}


def test_aliases_without_short_and_full_id():
    inspect = container(**{"NetworkSettings.Networks.deck_front.Aliases": [OLD_ID[:12], OLD_ID, "keep"]})
    assert build(inspect).connects[0][1] == {"Aliases": ["keep"]}


def test_unknown_endpoint_field():
    assert refusal(build, container(**{"NetworkSettings.Networks.deck_front.Future": "x"})).code == \
        policy.UNKNOWN_FIELD
    assert build(container(**{"NetworkSettings.Networks.deck_front.Future": None})).connects


def test_connects_are_sorted():
    inspect = container()
    inspect["NetworkSettings"]["Networks"]["deck_aaa"] = {"NetworkID": "d" * 64, "Aliases": None}
    plan = build(inspect, drivers={**DRIVERS, "d" * 64: "bridge"})
    assert [c[0] for c in plan.connects] == ["d" * 64, NET_A]


# ---------------------------------------------------------------------------
# Allgemein
# ---------------------------------------------------------------------------


def test_inputs_are_not_changed():
    inspect, image = container(), image_config()
    before = (copy.deepcopy(inspect), copy.deepcopy(image))
    plan = build(inspect, image)
    plan.body["HostConfig"]["Binds"].append("x")
    plan.body["Labels"]["x"] = "y"
    assert (inspect, image) == before


def test_synthetic_body_passes_the_engine_client_checks():
    Engine()._check_create_body(build().body)
    with pytest.raises(NotAllowed):
        Engine()._check_create_body({**build().body, "Entrypoint": []})


def test_is_default():
    for value in (None, False, 0, 0.0, "", [], {}):
        assert clone.is_default(value)
    for value in (True, 1, "x", [0], {"a": None}, {"": ""}, [None]):
        assert not clone.is_default(value)
    assert clone.struct_norm({"a": None, "b": {"c": ""}, "d": 1}) == {"d": 1}


# ---------------------------------------------------------------------------
# Nachkontrolle (synthetisch)
# ---------------------------------------------------------------------------


def new_image(**config):
    image = image_config(**config)
    image["Id"] = NEW_IMAGE
    image["Config"]["Labels"] = {**image["Config"]["Labels"], policy.VERSION_LABEL: "0.7.1"}
    return image


def test_verify_accepts_what_the_daemon_makes_of_the_body():
    old = container()
    plan = build(old)
    image = new_image()
    for started in (False, True):
        clone.verify(old, simulate_create(plan, image, old=old, started=started), plan, new_image=image,
                     new_image_id=NEW_IMAGE)
    clone.verify(old, simulate_create(plan, image, old=old, short_id_alias=True), plan, new_image=image,
                 new_image_id=NEW_IMAGE)


def test_new_image_values_apply_in_the_clone():
    old = container()
    plan = build(old)
    image = new_image(Env=["PATH=/usr/bin", "PYTHON_VERSION=3.13.1", "NODVARD_DECK_ENV=prod", "NEW=1"],
                      Cmd=["uvicorn", "new:app"], WorkingDir="/srv", User="1000",
                      Labels={policy.VERSION_LABEL: "0.7.1", "new.label": "x"}, ExposedPorts={"8081/tcp": {}},
                      Volumes={"/app/data": {}, "/cache": {}},
                      Healthcheck={"Test": ["CMD-SHELL", "new"], "Interval": 10_000_000_000, "Retries": 5})
    new = simulate_create(plan, image, old=old)
    clone.verify(old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    config = new["Config"]
    assert "PYTHON_VERSION=3.13.1" in config["Env"] and "NODVARD_DECK_AUDIT_RETENTION_DAYS=90" in config["Env"]
    assert config["Cmd"] == ["uvicorn", "new:app"] and config["WorkingDir"] == "/srv"
    assert config["Labels"][policy.VERSION_LABEL] == "0.7.1" and config["Labels"]["user.label"] == "mine"
    assert config["Healthcheck"]["Test"] == ["CMD", "python", "-c", "x"]  # der Test aus Compose bleibt
    assert config["Healthcheck"]["Interval"] == 10_000_000_000             # die Zeiten kommen aus dem neuen Image


def test_new_anonymous_volume_from_the_new_image_is_allowed_but_nothing_else():
    old = container()
    plan = build(old)
    image = new_image(Volumes={"/app/data": {}, "/cache": {}})
    new = simulate_create(plan, image, old=old)
    assert "/cache" in clone.mount_map(new) and "/cache" not in clone.mount_map(old)
    clone.verify(old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    new["Mounts"].append({"Type": "volume", "Name": "x", "Destination": "/other", "RW": True})
    assert refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE).detail == "mounts"
    new["Mounts"].pop()
    next(m for m in new["Mounts"] if m["Destination"] == "/cache").update(Type="bind")
    assert refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE).detail == "mounts"


def test_naive_clone_would_get_a_new_empty_data_volume():
    old = container()
    plan = build(old)
    naive = clone.Plan(name=plan.name, body=copy.deepcopy(plan.body), connects=plan.connects)
    del naive.body["HostConfig"]["Mounts"]
    image = new_image()
    new = simulate_create(naive, image, old=old)
    assert clone.mount_map(new)["/app/data"] != clone.mount_map(old)["/app/data"]
    assert refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE).detail == "mounts"


@pytest.mark.parametrize(("change", "detail"), [
    (lambda c: c.update(Image=OLD_IMAGE), "image"),
    (lambda c: c.update(Name="/x"), "name"),
    (lambda c: c["Mounts"].append({"Type": "bind", "Source": "/", "Destination": "/host", "RW": True}), "mounts"),
    (lambda c: c["HostConfig"].update(CapAdd=["SYS_ADMIN"]), "cap_add"),
    (lambda c: c["HostConfig"].update(Links=["/x:/y/z"]), "links"),
    (lambda c: c["HostConfig"].update(AutoRemove=True), "auto_remove"),
    (lambda c: c["HostConfig"].update(Unheard=1), "unheard"),
    (lambda c: c["Config"].update(Hostname="other"), None),  # Hostname war weggelassen: Abweichung erlaubt
    (lambda c: c["Config"]["Env"].remove("NODVARD_DECK_AUDIT_RETENTION_DAYS=90"), "env"),
    (lambda c: c["Config"].update(StopSignal="SIGKILL"), "stop_signal"),
    (lambda c: c["Config"].update(Domainname="x"), "domainname"),
    (lambda c: c["Config"].update(Unheard=1), "unheard"),
    (lambda c: c["NetworkSettings"]["Networks"]["deck_back"].update(IPAMConfig=None), "networks"),
])
def test_verify_rejects(change, detail):
    old = container()
    plan = build(old)
    image = new_image()
    new = simulate_create(plan, image, old=old)
    change(new)
    if detail is None:
        clone.verify(old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
        return
    exc = refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    assert (exc.code, exc.detail) == (policy.CLONE_MISMATCH, detail)


@pytest.mark.parametrize("path", clone.REQUIRED_MOUNTS)
def test_verify_needs_data_and_channel_mounts(path):
    # Daten und Kanal (/app/data und /app/updater) muessen eingehaengt sein: fehlt einer schon beim alten (Daten in
    # der Container-Schicht, kein Kanal) und bringt das neue Image dort ein anonymes Volume mit, ist das kein gueltiger
    # Klon.
    old = container()
    old["Mounts"] = [m for m in old["Mounts"] if m["Destination"] != path]
    old["HostConfig"]["Binds"] = [b for b in old["HostConfig"]["Binds"] if not b.endswith(path + ":rw")]
    plan = build(old)
    image = new_image(Volumes={"/app/data": {}, "/app/updater": {}})
    new = simulate_create(plan, image, old=old)
    assert path in clone.mount_map(new)  # das neue Image haengt dort ein frisches Volume ein
    exc = refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    assert (exc.code, exc.detail) == (policy.CLONE_MISMATCH, "mounts")


def test_verify_checks_the_channel_of_the_helper():
    old = container()
    plan = build(old)
    image = new_image()
    new = simulate_create(plan, image, old=old)
    channel = target.MountRef(kind="volume", name="deck_updater", source="/var/lib/docker/v/deck_updater/_data")
    clone.verify(old, new, plan, new_image=image, new_image_id=NEW_IMAGE, channel=channel.origin())
    other = target.MountRef(kind="volume", name="other_updater", source=None)
    exc = refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE, channel=other.origin())
    assert (exc.code, exc.detail) == (policy.CLONE_MISMATCH, "mounts")
    bind = target.MountRef(kind="bind", name=None, source="/var/lib/docker/v/deck_updater/_data")
    assert refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE,
                   channel=bind.origin()).detail == "mounts"
    assert clone.CHANNEL_PATH == target.CHANNEL_IN_TARGET


def test_verify_keeps_a_hostname_that_was_set():
    old = container(**{"Config.Hostname": "deck"})
    plan = build(old)
    image = new_image()
    new = simulate_create(plan, image, old=old)
    clone.verify(old, new, plan, new_image=image, new_image_id=NEW_IMAGE)
    new["Config"]["Hostname"] = NEW_ID[:12]
    assert refusal(clone.verify, old, new, plan, new_image=image, new_image_id=NEW_IMAGE).detail == "hostname"


def test_verify_needs_the_new_image_config():
    old = container()
    plan = build(old)
    new = simulate_create(plan, new_image(), old=old)
    assert refusal(clone.verify, old, new, plan, new_image={"Id": NEW_IMAGE},
                   new_image_id=NEW_IMAGE).code == policy.IMAGE_CONFIG_MISSING


def test_merged_env():
    assert clone.merged_env(["A=1", "B=2"], ["A=0", "C=3", "B"]) == ["A=1", "B=2", "C=3"]


# ---------------------------------------------------------------------------
# Werkzeug zum Aufzeichnen
# ---------------------------------------------------------------------------


def _record_tool():
    import importlib.util

    from updater_support import UPDATER_DIR
    spec = importlib.util.spec_from_file_location("record_engine", UPDATER_DIR / "tools" / "record_engine.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_record_tool_is_not_part_of_the_package():
    from updater_support import UPDATER_DIR
    assert (UPDATER_DIR / "tools" / "record_engine.py").exists()
    assert not list((UPDATER_DIR / "nodvard_deck_updater").rglob("record*"))


def test_record_tool_only_reads_and_scrubs():
    from fake_engine import FakeEngine, World
    tool = _record_tool()
    world = World.from_fixture("docker29-api154")
    target = world.by_service("nodvard-deck")
    target["Config"]["Env"].append("NODVARD_DECK_JWT_SECRET=" + SECRET)
    target["Config"]["Labels"]["traefik.http.middlewares.auth.basicauth.users"] = "nico:$apr1$" + SECRET
    target["HostConfig"]["LogConfig"] = {"Type": "loki", "Config": {"loki-url": "https://u:" + SECRET + "@x"}}
    target["State"]["Health"] = {"Status": "healthy", "Log": [{"Output": SECRET, "ExitCode": 0}]}
    with FakeEngine(world) as fake:
        data = tool.record(fake.path, "u2probe", None, [])
        forced = tool.record(fake.path, "u2probe", "1.43", [])
    assert fake.methods == {"GET"} and fake.violations == []
    text = json.dumps(data)
    assert SECRET not in text and SECRET not in json.dumps(forced)
    assert forced["api"] == "1.43" and data["api"] == "1.54"
    recorded_target = data["inspect"][target["Id"]]
    assert "NODVARD_DECK_JWT_SECRET=scrubbed-" in " ".join(recorded_target["Config"]["Env"])
    assert recorded_target["Config"]["Labels"]["com.docker.compose.service"] == "nodvard-deck"
    # gleicher Wert -> gleiches Kennzeichen (Vergleiche bleiben erhalten), leerer Wert bleibt leer
    scrubber = tool.Scrubber()
    assert scrubber.env(["A=x", "B=x", "C=", "D=y"]) == ["A=scrubbed-1", "B=scrubbed-1", "C=", "D=scrubbed-2"]


def private_inspect():
    """Ein Inspect mit allem, was ein echter Rechner mitbringt: Heimatordner, Adressen im Heimnetz, Rechnernamen."""
    return {
        "Id": "a" * 64,
        "Config": {
            "Hostname": "meinserver", "Domainname": "heim.lan",
            "Labels": {"com.docker.compose.service": "web",
                       "com.docker.compose.project.working_dir": "/home/nico/stack",
                       "com.docker.compose.project.config_files":
                           "/home/nico/stack/compose.yml,/home/nico/stack/override.yml",
                       "com.docker.compose.project.environment_file": "/home/nico/stack/.env"},
        },
        "HostConfig": {
            "Binds": ["/home/nico/stack/data:/data:rw", "/var/run/docker.sock:/var/run/docker.sock", "named_vol:/x",
                      "/mnt/nas/media:/media:ro", "C:\\Users\\nico\\stack:/win"],
            "Dns": ["10.0.0.53", "192.168.2.1"], "DnsSearch": ["fritz.box", "heim.lan"],
            "ExtraHosts": ["nas.lan:192.168.2.20", "host.docker.internal:host-gateway", "lokal:127.0.0.1"],
            "PortBindings": {"8080/tcp": [{"HostIp": "192.168.2.5", "HostPort": "8080"}]},
            "Mounts": [{"Type": "bind", "Source": "/mnt/nas/media", "Target": "/media"},
                       {"Type": "volume", "Source": "named_vol", "Target": "/y"}],
        },
        "LogPath": "/home/nico/docker/containers/log.json",
        "Mounts": [
            {"Type": "bind", "Source": "/mnt/nas/media", "Destination": "/media", "RW": False},
            {"Type": "bind", "Source": "/srv/proxy/docker.sock", "Destination": "/var/run/docker.sock", "RW": True},
            {"Type": "volume", "Name": "named_vol", "Source": "/var/lib/docker/volumes/named_vol/_data",
             "Destination": "/x", "RW": True},
        ],
        "NetworkSettings": {"Networks": {"back": {
            "IPAddress": "10.8.0.4", "Gateway": "10.8.0.1", "IPPrefixLen": 24, "MacAddress": "3c:52:82:11:22:33",
            "GlobalIPv6Address": "2a02:1234:5678::4", "IPv6Gateway": "fe80::1",
            "IPAMConfig": {"IPv4Address": "10.8.0.4"},
            "DriverOpts": {"o": "addr=192.168.2.30,rw", "device": ":/export/x"},
            "Aliases": ["stack-web-1", "web", "a" * 12, "meinserver"],
            "DNSNames": ["stack-web-1", "web", "a" * 12, "meinserver"],
        }}},
        "IPAM": {"Config": [{"Subnet": "10.8.0.0/24", "Gateway": "10.8.0.1"}, {"Subnet": "100.64.1.0/24"}]},
        "Status": {"IPAM": {"Subnets": {"10.8.0.0/24": {"DynamicIPsAvailable": 250}}}},
    }


PRIVATE_VALUES = ("nico", "/mnt/nas", "/srv/proxy", "192.168.2.", "10.0.0.", "10.8.0.", "100.64.", "3c:52:82", "2a02:",
                  "fe80:", "meinserver", "heim.lan", "fritz.box", "nas.lan", "Users", "/export/x")


def test_record_tool_scrubs_paths_addresses_and_hostnames():
    tool = _record_tool()
    data = private_inspect()
    scrubbed = tool.Scrubber().walk(data)
    text = json.dumps(scrubbed)
    for private in PRIVATE_VALUES:
        assert private not in text, private
    assert tool.find_leaks(scrubbed) == []
    assert tool.find_leaks(data) != []  # die Gegenprobe selbst schlaegt bei den Rohdaten an
    assert data == private_inspect()  # die Eingabe bleibt unveraendert


def test_record_tool_scrubbing_keeps_what_the_helper_depends_on():
    tool = _record_tool()
    out = tool.Scrubber().walk(private_inspect())
    config, host, mounts = out["Config"], out["HostConfig"], out["Mounts"]
    labels = config["Labels"]
    # Pfade: gleicher Pfad, gleiche Ersetzung -- Binds und Mounts bleiben vergleichbar, die Verschachtelung bleibt.
    assert host["Binds"][3].split(":")[0] == mounts[0]["Source"] == host["Mounts"][0]["Source"]
    assert labels["com.docker.compose.project.working_dir"] != "/home/nico/stack"
    workdir = labels["com.docker.compose.project.working_dir"]
    files = labels["com.docker.compose.project.config_files"].split(",")
    assert len(files) == 2 and all(f.startswith(workdir + "/") for f in files)
    assert labels["com.docker.compose.project.environment_file"].startswith(workdir + "/")
    # der Docker-Socket (auch unter anderem Pfad, das Ziel prueft auf den Namen), Volume-Namen und der
    # Standardordner des Docker-Dienstes bleiben, damit die Vorpruefung dieselben Schluesse zieht
    assert host["Binds"][1] == "/var/run/docker.sock:/var/run/docker.sock" and host["Binds"][2] == "named_vol:/x"
    assert mounts[1]["Source"].endswith("/docker.sock") and "proxy" not in mounts[1]["Source"]
    assert mounts[2]["Source"] == "/var/lib/docker/volumes/named_vol/_data"
    assert host["Binds"][4].endswith(":/win") and "nico" not in host["Binds"][4]
    assert host["Mounts"][1]["Source"] == "named_vol"  # ein Volume-Name als Quelle ist kein Pfad und bleibt
    # Adressen: dieselbe Adresse -> dieselbe Ersetzung, dasselbe /24-Netz bleibt ein Netz, Laenge und Loopback bleiben
    nic = out["NetworkSettings"]["Networks"]["back"]
    assert nic["IPAddress"] == nic["IPAMConfig"]["IPv4Address"] != "10.8.0.4"
    assert nic["IPAddress"].rpartition(".")[0] == nic["Gateway"].rpartition(".")[0] and nic["IPPrefixLen"] == 24
    assert nic["IPAddress"].endswith(".4") and nic["Gateway"].endswith(".1")
    (subnet,) = out["Status"]["IPAM"]["Subnets"]  # auch ein Schluessel kann eine Adresse sein
    assert subnet == out["IPAM"]["Config"][0]["Subnet"] and subnet.endswith(".0/24")
    assert nic["DriverOpts"]["o"].startswith("addr=198.18.") and nic["DriverOpts"]["o"].endswith(",rw")
    assert host["ExtraHosts"][2].endswith(":127.0.0.1") and not host["ExtraHosts"][2].startswith("lokal")
    assert host["ExtraHosts"][0].startswith("host-") and "192.168." not in host["ExtraHosts"][0]
    assert host["ExtraHosts"][1] == "host.docker.internal:host-gateway"
    assert host["PortBindings"]["8080/tcp"][0]["HostIp"].startswith("198.18.")
    # Rechnernamen: gleicher Name, gleiches Kennzeichen; die Kurz-ID als Hostname ist der Standard und bleibt
    assert config["Domainname"] == host["DnsSearch"][1] and config["Hostname"] != "meinserver"
    # `DNSNames` und `Aliases` (aeltere API-Versionen) wiederholen den Hostname (ersetzt wie dort); Containername,
    # Alias und Kurz-ID bleiben
    assert nic["DNSNames"] == ["stack-web-1", "web", "a" * 12, config["Hostname"]]
    assert nic["Aliases"] == ["stack-web-1", "web", "a" * 12, config["Hostname"]]
    assert tool.Scrubber().hostname("0123456789ab") == "0123456789ab"
    scrubber = tool.Scrubber()
    assert scrubber.walk({"Dns": ["10.1.1.1", "10.1.1.2"]}) == {"Dns": ["198.18.0.1", "198.18.0.2"]}
    assert scrubber.ipv4("10.1.1.1") == "198.18.0.1" and scrubber.ipv4("10.9.9.9") == "198.18.1.9"


def test_record_tool_dns_names_follow_the_hostname_in_any_order():
    # Die Engine schreibt `Config` vor `NetworkSettings`; die Ersetzung in `DNSNames` haengt nicht daran.
    inspect = {"NetworkSettings": {"Networks": {"n": {"DNSNames": ["stack-web-1", "meinserver"]}}},
               "Config": {"Hostname": "meinserver"}}
    out = _record_tool().Scrubber().walk(inspect)
    assert out["NetworkSettings"]["Networks"]["n"]["DNSNames"] == ["stack-web-1", out["Config"]["Hostname"]]
    assert "meinserver" not in json.dumps(out)


def test_record_tool_aliases_follow_the_hostname_in_any_order():
    # Bei aelteren API-Versionen haengt Docker die Kurz-ID und den eigenen Hostname eines Containers in
    # benutzerdefinierten Netzen auch an `Aliases` an (ein alter Docker-Dienst ohne `DNSNames` nennt ihn nur dort).
    # Auch hier ersetzt, unabhaengig von der Reihenfolge.
    tool = _record_tool()
    short_id = "0123456789ab"
    inspect = {"NetworkSettings": {"Networks": {"n": {"Aliases": ["stack-web-1", "web", short_id, "meinserver"]},
                                                "m": {"Aliases": None}, "o": {"Aliases": []}}},
               "Config": {"Hostname": "meinserver"}}
    out = tool.Scrubber().walk(inspect)
    networks = out["NetworkSettings"]["Networks"]
    assert networks["n"]["Aliases"] == ["stack-web-1", "web", short_id, out["Config"]["Hostname"]]
    assert networks["m"]["Aliases"] is None and networks["o"]["Aliases"] == []
    assert out["Config"]["Hostname"].startswith("host-") and "meinserver" not in json.dumps(out)
    # Ohne eigenen Rechnernamen (Standard: die Kurz-ID) bleibt die Kurz-ID als Alias stehen.
    plain = {"Config": {"Hostname": short_id}, "NetworkSettings": {"Networks": {"n": {"Aliases": ["web", short_id]}}}}
    assert tool.Scrubber().walk(plain) == plain
    # Ein Alias, der kein Rechnername des Containers ist, bleibt.
    other = {"Config": {"Hostname": "meinserver"}, "NetworkSettings": {"Networks": {"n": {"Aliases": ["db"]}}}}
    assert tool.Scrubber().walk(other)["NetworkSettings"]["Networks"]["n"]["Aliases"] == ["db"]


@pytest.mark.parametrize("text", [
    "192.168.2.5", "10.1.2.3", "x=10.255.255.255:8080", "100.64.0.1", "100.127.255.254", "169.254.1.1",
    "/home/nico", "/home/nico/stack:/data", "/Users/nico/x", "/root", "/root/.ssh", "/mnt:/home/u:ro",
    "3c:52:82:11:22:33", "2a02:1234::1", "fd00::1", "fe80::1",
])
def test_record_tool_finds_what_does_not_belong(text):
    tool = _record_tool()
    kinds = tool.leak_kinds(text)
    assert kinds
    found = tool.find_leaks({"inspect": {"k": ["x", text]}})
    assert found == [f"$.inspect.k[1]: {kind}" for kind in kinds]  # Ort und Art, nie der Wert
    assert tool.find_leaks({text: "x"}) == [f"$.{text} (Schluessel): {kind}" for kind in kinds]


@pytest.mark.parametrize("text", [
    "172.18.0.2/16", "172.31.251.0/24", "127.0.0.1", "0.0.0.0", "198.18.0.5", "100.63.255.1", "100.128.0.1", "11.0.0.1",
    "110.1.2.3", "1.54", "0.7.1", "1.2.3.4.5", "/homepage", "/srv/home/x", "/var/lib/docker/volumes/x/_data",
    "/app/data", "02:42:ac:12:00:02", "02:00:00:00:00:01", "::1", "::", "2001:db8::1", "sha256:" + "ab" * 32,
    "2026-10-02T10:20:30.123456789Z", "host.docker.internal:host-gateway",
])
def test_record_tool_does_not_flag_harmless_values(text):
    assert _record_tool().leak_kinds(text) == []


@pytest.mark.parametrize("text", [
    "127.0.0.1", "0.0.0.0", "127.0.0.1:8080", "1.54", "0.7.1", "1.2.3.4.5", "/homepage", "/app/data",
    "/var/lib/docker/volumes/x/_data", "::1", "::", "sha256:" + "ab" * 32, "2026-10-02T10:20:30.123456789Z",
    "host.docker.internal:host-gateway", "8080/tcp", "u2probe_back", "https://example.org/x:1",
])
def test_record_tool_scrubber_leaves_other_text_alone(text):
    # Nur gueltige Adressen werden ersetzt; Versionen, Zeitstempel, Pruefsummen und Pfade im Container bleiben.
    assert _record_tool().Scrubber().text(text) == text


def test_record_tool_output_has_no_host_details():
    # Von Anfang bis Ende: ein Rechner mit Heimatordner, Heimnetz-Adressen und eigenem Rechnernamen -- in der
    # Aufnahme steht davon nichts, und die Zusammenhaenge (Binds gleich Mounts) bleiben.
    from fake_engine import FakeEngine, World
    tool = _record_tool()
    world = World.from_fixture("docker29-api154")
    target = world.by_service("nodvard-deck")
    target["Config"]["Labels"]["com.docker.compose.project.working_dir"] = "/home/nico/stack"
    target["Config"]["Labels"]["com.docker.compose.project.config_files"] = "/home/nico/stack/compose.yml"
    target["Config"]["Hostname"] = "meinserver"
    target["HostConfig"]["Dns"] = ["192.168.2.1"]
    target["HostConfig"]["Binds"] = ["/home/nico/stack/bind:/probe-bind:ro", "u2probe-updater:/app/updater:rw"]
    for mount in target["Mounts"]:
        if mount["Type"] == "bind":
            mount["Source"] = "/home/nico/stack/bind"
    for endpoint in target["NetworkSettings"]["Networks"].values():
        endpoint["IPAddress"], endpoint["Gateway"] = "192.168.2.50", "192.168.2.1"
        endpoint["MacAddress"] = "3c:52:82:11:22:33"
        endpoint["DNSNames"] = [*(endpoint["DNSNames"] or []), "meinserver"]  # so meldet es die Engine
        endpoint["Aliases"] = [*(endpoint["Aliases"] or []), "meinserver"]  # ... und aeltere API-Versionen
    with FakeEngine(world) as fake:
        data = tool.record(fake.path, "u2probe", None, [])
    text = json.dumps(data)
    for private in ("nico", "192.168.2.", "3c:52:82", "meinserver"):
        assert private not in text, private
    recorded = data["inspect"][target["Id"]]
    (source,) = {m["Source"] for m in recorded["Mounts"] if m["Type"] == "bind"}
    assert recorded["HostConfig"]["Binds"][0] == f"{source}:/probe-bind:ro"
    assert tool.find_leaks(data) == []


def test_record_tool_writes_nothing_when_something_is_left():
    # Was die Bereinigung nicht kennt (hier: ein Heimatordner als Arbeitsordner im Container), landet nicht in der
    # Datei; die Meldung nennt den Ort, nie den Wert.
    from fake_engine import FakeEngine, World
    tool = _record_tool()
    world = World.from_fixture("docker29-api154")
    world.by_service("nodvard-deck")["Config"]["WorkingDir"] = "/home/node/app"
    with FakeEngine(world) as fake, pytest.raises(SystemExit) as exc:
        tool.record(fake.path, "u2probe", None, [])
    message = str(exc.value)
    assert "Nichts geschrieben" in message and "WorkingDir" in message and "Heimatordner" in message
    assert "/home/node" not in message


def test_record_tool_regenerates_the_same_goldens(tmp_path):
    import shutil
    tool = _record_tool()
    for name in tool.GOLDEN_NAMES:
        shutil.copy(FIXTURES / f"{name}.json", tmp_path / f"{name}.json")
    for path in tool.write_goldens(tmp_path):
        assert path.read_text(encoding="ascii") == (FIXTURES / path.name).read_text(encoding="ascii"), path.name


def test_record_tool_refuses_anything_but_get():
    from fake_engine import FakeEngine, World
    tool = _record_tool()
    with FakeEngine(World()) as fake:
        engine = tool.read_only_engine(fake.path)
        engine.negotiate()
        engine.list_containers()
        for call in (lambda: engine.remove_container("a" * 64), lambda: engine.start_container("a" * 64),
                     lambda: engine.pull("sha256:" + "d" * 64)):
            with pytest.raises(SystemExit):
                call()
    assert fake.methods == {"GET"}
