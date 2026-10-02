"""Vorpruefung und Zielauswahl.

Grundlage ist eine an der echten Engine aufgezeichnete Welt (Compose-Projekt mit Dashboard und Helfer, siehe
`fixtures/engines/`), die die Fake-Engine ueber einen Unix-Socket ausliefert. Jeder Test veraendert genau eine Sache
und erwartet genau einen Ablehnungscode. Die Vorpruefung liest nur: jede Anfrage ist `GET`.
"""

from __future__ import annotations

import copy
import os

import pytest
from fake_engine import FakeEngine, Response, World
from nodvard_deck_updater import engine as E
from nodvard_deck_updater import policy, target
from nodvard_deck_updater.policy import Refusal

RECORDED = ["docker29-api154", "docker29-api143", "docker29-api141", "docker29-api154-stack", "docker29-api154-anon"]


class Lab:
    """Eine Welt aus der Aufzeichnung plus bequeme Zugriffe; `run()` startet die Vorpruefung."""

    def __init__(self, name: str = "docker29-api154") -> None:
        self.world = World.from_fixture(name)
        self.helper = self.world.by_service("updater")
        self.target = self.world.by_service("nodvard-deck")
        self.image = self.world.images[self.target["Image"]]
        self.routes: list[tuple[str, str, Response]] = []
        self.fake: FakeEngine | None = None

    def run(self, *, self_id: object = ..., service: str = "nodvard-deck", exclude=(), engine=None):
        with FakeEngine(self.world) as fake:
            self.fake = fake
            for method, pattern, response in self.routes:
                fake.route(method, pattern, response)
            result = target.preflight(engine or E.Engine(fake.path),
                                      self_id=self.helper["Id"] if self_id is ... else self_id,
                                      service=service, exclude=exclude)
        assert fake.violations == []
        assert fake.methods <= {"GET"}, fake.methods  # die Vorpruefung liest nur
        assert not fake.calls("GET", r"/distribution/.*"), "die Vorpruefung fragt nie die Registry"
        return result

    def duplicate(self, **state) -> dict:
        twin = copy.deepcopy(self.target)
        twin["Id"] = "e" * 64
        twin["Name"] = "/u2probe-nodvard-deck-2"
        twin["State"].update(state)
        return self.world.add_container(twin)


@pytest.fixture
def lab():
    return Lab()


def code(result):
    assert not result.ready and result.target is None
    return result.reason


# ---------------------------------------------------------------------------
# Bereit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", RECORDED)
def test_recorded_setups_are_ready(name):
    lab = Lab(name)
    result = lab.run()
    assert result.ready and result.reason is None
    found = result.target
    assert found.id == lab.target["Id"] and found.name == lab.target["Name"][1:]
    assert found.image_id == lab.target["Image"] and found.version == "0.7.0" and found.floating_tag == "latest"
    assert found.repo_digests and all(d.startswith(policy.REPOSITORY + "@sha256:") for d in found.repo_digests)
    assert found.restart_policy[0] in ("unless-stopped", "always")
    assert "Entrypoint" not in found.plan.body  # der Body wird schon hier probeweise gebaut
    assert result.status_target() == {"current_version": "0.7.0", "floating_tag": "latest", "pinned": False}


def test_preflight_negotiates_and_uses_the_full_id(lab):
    lab.run()
    paths = [r.path for r in lab.fake.requests]
    assert paths[:2] == ["/_ping", "/version"]
    assert f"/containers/{lab.target['Id']}/json" in paths
    listing = lab.fake.calls("GET", "/containers/json")[0]
    assert listing.query["filters"] == [(
        '{"label":["com.docker.compose.project=u2probe","com.docker.compose.service=nodvard-deck",'
        '"com.docker.compose.oneoff=False"]}')]


def test_other_service_name(lab):
    lab.target["Config"]["Labels"]["com.docker.compose.service"] = "deck"  # NODVARD_DECK_UPDATER_SERVICE=deck
    assert lab.run(service="deck").ready
    assert code(lab.run()) == policy.NO_TARGET


# ---------------------------------------------------------------------------
# Einrichtung: Engine, eigene ID, Compose
# ---------------------------------------------------------------------------


def test_engine_missing(tmp_path, lab):
    result = target.preflight(E.Engine(str(tmp_path / "x.sock")), self_id=lab.helper["Id"], service="nodvard-deck")
    assert (result.reason, result.detail) == (policy.ENGINE_UNREACHABLE, "unreachable")


def test_engine_error_while_listing(lab):
    lab.routes.append(("GET", "/containers/json", Response(status=500, body={"message": "boom"})))
    assert code(lab.run()) == policy.ENGINE_UNREACHABLE


def test_engine_answers_garbage(lab):
    lab.routes.append(("GET", "/containers/json", Response(body=b"<html>", content_type="text/html")))
    assert code(lab.run()) == policy.ENGINE_UNSUPPORTED


def test_podman(lab):
    lab.world.version["Components"] = [{"Name": "Podman Engine"}]
    assert code(lab.run()) == policy.ENGINE_UNSUPPORTED


def test_api_too_old(lab):
    lab.world.api = "1.40"
    assert code(lab.run()) == policy.API_TOO_OLD


@pytest.mark.parametrize("self_id", [None, "", "abc", "A" * 64, "f" * 64])
def test_self_unknown(lab, self_id):
    assert code(lab.run(self_id=self_id)) == policy.SELF_UNKNOWN


def test_self_inspect_returns_another_id(lab):
    lab.routes.append(("GET", f"/containers/{lab.helper['Id']}/json", Response(body={**lab.helper, "Id": "f" * 64})))
    assert code(lab.run()) == policy.SELF_UNKNOWN


@pytest.mark.parametrize("project", [None, "", "Bad Name", "../x", "x" * 200])
def test_not_compose(lab, project):
    labels = lab.helper["Config"]["Labels"]
    if project is None:
        del labels["com.docker.compose.project"]
    else:
        labels["com.docker.compose.project"] = project
    assert code(lab.run()) == policy.NOT_COMPOSE


def test_helper_in_another_project_finds_no_target(lab):
    lab.helper["Config"]["Labels"]["com.docker.compose.project"] = "helper-only"
    assert code(lab.run()) == policy.NO_TARGET


# ---------------------------------------------------------------------------
# Ziel finden
# ---------------------------------------------------------------------------


def test_no_target(lab):
    del lab.world.containers[lab.target["Id"]]
    result = lab.run()
    assert code(result) == policy.NO_TARGET and result.status_target() is None


def test_oneoff_container_is_no_target(lab):
    lab.target["Config"]["Labels"]["com.docker.compose.oneoff"] = "True"  # `compose run` (z. B. backup.sh)
    assert code(lab.run()) == policy.NO_TARGET


def test_oneoff_next_to_the_target_is_ignored(lab):
    lab.duplicate()["Config"]["Labels"]["com.docker.compose.oneoff"] = "True"
    assert lab.run().ready


def test_engine_ignoring_the_filter_does_not_lead_to_a_wrong_target(lab):
    stranger = copy.deepcopy(lab.target)
    stranger["Id"] = "9" * 64
    stranger["Config"]["Labels"]["com.docker.compose.project"] = "other"
    lab.world.add_container(stranger)
    everything = [lab.world.summary(c) for c in lab.world.containers.values()]
    lab.routes.append(("GET", "/containers/json", Response(body=everything)))
    result = lab.run()
    assert result.ready and result.target.id == lab.target["Id"]


@pytest.mark.parametrize("state", [{}, {"Status": "exited", "Running": False}, {"Status": "created", "Running": False}])
def test_doppelgaenger_means_multiple_targets(lab, state):
    lab.duplicate(**state)  # auch ein gestoppter Doppelgaenger (z. B. ein liegen gebliebener `-previous`)
    assert code(lab.run()) == policy.MULTIPLE_TARGETS


def test_with_a_journal_the_containers_of_the_journal_are_no_target(lab):
    # Journal-Stand `created`: der alte (laeuft) und der neue Container stehen im Journal, beide tragen die Labels.
    # Es gibt dann kein Ziel mehr (`no_target`); der Ablauf arbeitet ueber die IDs aus dem Journal.
    new = lab.duplicate(Status="created", Running=False)
    result = lab.run(exclude=[lab.target["Id"], new["Id"]])
    assert code(result) == policy.NO_TARGET


def test_a_stranger_with_the_target_labels_is_never_the_target_with_a_journal(lab):
    # Das echte Dashboard steht im Journal; ein Fremder mit gefaelschten Compose-Labels (healthy, gleiches Image und
    # Kanal-Volume) darf nicht das "bereite" Ziel werden. Ohne Journal hiesse dieselbe Lage `multiple_targets`.
    stranger = lab.duplicate()
    assert code(lab.run()) == policy.MULTIPLE_TARGETS
    result = lab.run(exclude=[lab.target["Id"]])
    assert code(result) == policy.EXTERNAL_CHANGE
    assert not result.known and result.status_target() is None
    # Auch wenn nur der Fremde uebrig ist (das echte Ziel ist verschwunden), wird er kein Ziel.
    lab.world.containers.pop(lab.target["Id"])
    assert code(lab.run(exclude=[lab.target["Id"]])) == policy.EXTERNAL_CHANGE
    assert stranger["Id"] in lab.world.containers


def test_swarm_labels_still_win_over_the_journal_rule(lab):
    lab.duplicate()["Config"]["Labels"]["com.docker.swarm.task.id"] = "y"
    assert code(lab.run(exclude=[lab.target["Id"]])) == policy.SWARM


def test_the_helper_itself_never_counts(lab):
    lab.helper["Config"]["Labels"]["com.docker.compose.service"] = "nodvard-deck"
    assert lab.run().ready


@pytest.mark.parametrize("where", ["target", "twin"])
def test_swarm_labels(lab, where):
    if where == "target":
        lab.target["Config"]["Labels"]["com.docker.swarm.service.id"] = "x"
    else:
        lab.duplicate()["Config"]["Labels"]["com.docker.swarm.task.id"] = "y"
    assert code(lab.run()) == policy.SWARM


def test_target_vanishes_between_list_and_inspect(lab):
    lab.routes.append(("GET", f"/containers/{lab.target['Id']}/json", Response(status=404, body={"message": "x"})))
    assert code(lab.run()) == policy.NO_TARGET


def test_target_labels_change_between_list_and_inspect(lab):
    changed = copy.deepcopy(lab.target)
    changed["Config"]["Labels"]["com.docker.compose.project"] = "other"
    lab.routes.append(("GET", f"/containers/{lab.target['Id']}/json", Response(body=changed)))
    assert code(lab.run()) == policy.NO_TARGET


def test_bad_id_in_the_list(lab):
    listing = [{**lab.world.summary(lab.target), "Id": "short"}]
    lab.routes.append(("GET", "/containers/json", Response(body=listing)))
    assert code(lab.run()) == policy.ENGINE_UNSUPPORTED


# ---------------------------------------------------------------------------
# Laufen und Gesundheit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", [
    {"Running": False, "Status": "exited"}, {"Restarting": True, "Status": "restarting"},
    {"Paused": True, "Status": "paused"}, {"Status": "created", "Running": False},
])
def test_target_not_running(lab, state):
    lab.target["State"].update(state)
    assert code(lab.run()) == policy.TARGET_NOT_RUNNING


@pytest.mark.parametrize("health", ["starting", "unhealthy", "none", None])
def test_target_unhealthy(lab, health):
    lab.target["State"]["Health"]["Status"] = health
    assert code(lab.run()) == policy.TARGET_UNHEALTHY


@pytest.mark.parametrize("change", [
    lambda t: t["Config"].pop("Healthcheck"),
    lambda t: t["Config"].update(Healthcheck={"Test": ["NONE"]}),
    lambda t: t["Config"].update(Healthcheck={"Test": []}),
    lambda t: t["State"].pop("Health"),
])
def test_no_healthcheck(lab, change):
    change(lab.target)
    assert code(lab.run()) == policy.NO_HEALTHCHECK


# ---------------------------------------------------------------------------
# Image und Tag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("image", [
    "nodvard-deck:pi-3f2a1b0", "nodvard-deck:latest", "docker.io/nodvard/deck:latest", "GHCR.IO/nodvard/deck",
    "ghcr.io:443/nodvard/deck", "ghcr.io/nodvard/deck-evil:latest", "ghcr.io/nodvard/deck:beta",
    "ghcr.io/nodvard/deck:0.7.0-rc1",
])
def test_foreign_image(lab, image):
    lab.target["Config"]["Image"] = image
    result = lab.run()
    assert code(result) == policy.FOREIGN_IMAGE
    assert result.status_target() == {"current_version": None, "floating_tag": None, "pinned": False}


@pytest.mark.parametrize("image", ["ghcr.io/nodvard/deck:0.7.0", "ghcr.io/nodvard/deck@sha256:" + "a" * 64,
                                   "ghcr.io/nodvard/deck:latest@sha256:" + "a" * 64])
def test_pinned_version_still_reports_the_running_version(lab, image):
    lab.target["Config"]["Image"] = image
    result = lab.run()
    assert code(result) == policy.PINNED_VERSION
    assert result.status_target() == {"current_version": "0.7.0", "floating_tag": None, "pinned": True}


def test_minor_tag(lab):
    lab.target["Config"]["Image"] = "ghcr.io/nodvard/deck:0.7"
    result = lab.run()
    assert result.ready and result.floating_tag == "0.7" and result.target.floating_tag == "0.7"


@pytest.mark.parametrize("digests", [[], None, ["nodvard-deck@sha256:" + "a" * 64], ["ghcr.io/nodvard/deck@sha1:x"]])
def test_not_from_registry(lab, digests):
    lab.image["RepoDigests"] = digests
    assert code(lab.run()) == policy.NOT_FROM_REGISTRY


@pytest.mark.parametrize("version", [None, "", "0.7", "0.7.0-rc1", "v0.7.0", "0.7.١"])
def test_no_version_label(lab, version):
    labels = lab.image["Config"]["Labels"]
    if version is None:
        del labels[policy.VERSION_LABEL]
    else:
        labels[policy.VERSION_LABEL] = version
    assert code(lab.run()) == policy.NO_VERSION_LABEL


def test_version_label_on_the_container_does_not_count(lab):
    # Nur das Label des Images zaehlt, nie das (eingefrorene) Container-Label.
    del lab.image["Config"]["Labels"][policy.VERSION_LABEL]
    lab.target["Config"]["Labels"][policy.VERSION_LABEL] = "0.7.0"
    assert code(lab.run()) == policy.NO_VERSION_LABEL


def test_version_too_old(lab):
    lab.image["Config"]["Labels"][policy.VERSION_LABEL] = "0.6.9"
    result = lab.run()
    assert code(result) == policy.VERSION_TOO_OLD and result.current_version == "0.6.9"


@pytest.mark.parametrize("change", [
    lambda lab: lab.world.images.pop(lab.target["Image"]),
    lambda lab: lab.image.pop("Config"),
    lambda lab: lab.image.update(Config=None),
    lambda lab: lab.target.update(Image="ghcr.io/nodvard/deck:latest"),
])
def test_image_config_missing(lab, change):
    change(lab)
    assert code(lab.run()) == policy.IMAGE_CONFIG_MISSING


# ---------------------------------------------------------------------------
# Zustand, Socket und Kanal im Ziel
# ---------------------------------------------------------------------------


def _bind(source, destination, rw=True):
    return {"Type": "bind", "Source": source, "Destination": destination, "Mode": "", "RW": rw, "Propagation": ""}


@pytest.mark.parametrize(("mount", "detail"), [
    (_bind("/var/run/docker.sock", "/var/run/docker.sock"), "host_path"),
    (_bind("/var/run/docker.sock", "/tmp/engine", rw=False), "host_path"),  # `:ro` schuetzt an einem Socket nicht
    (_bind("/run/docker.sock", "/x"), "host_path"),                         # /var/run ist ein Link auf /run
    (_bind("/var/run", "/hostrun"), "host_path"),                           # Elternordner
    (_bind("/run", "/hostrun"), "host_path"),
    (_bind("/", "/host"), "host_path"),
    (_bind("/srv/other/docker.sock", "/x"), "socket"),                      # irgendein anderer Engine-Socket
    (_bind("/srv/x", "/run/docker.sock"), "socket"),
    (_bind("/var/lib/docker", "/docker"), "host_path"),                     # sieht das /state-Volume des Helfers
])
def test_unsafe_target_with_socket_or_state(lab, mount, detail):
    lab.target["Mounts"].append(mount)
    result = lab.run()
    assert (code(result), result.detail) == (policy.UNSAFE_TARGET, detail)


def test_unsafe_target_with_the_state_volume(lab):
    lab.target["Mounts"].append({"Type": "volume", "Name": "u2probe-state", "Source": "/elsewhere",
                                 "Destination": "/data2", "RW": True})
    result = lab.run()
    assert (code(result), result.detail) == (policy.UNSAFE_TARGET, "state_volume")


def test_unsafe_target_with_the_state_volume_path(lab):
    state = next(m for m in lab.helper["Mounts"] if m["Destination"] == "/state")
    lab.target["Mounts"].append(_bind(state["Source"], "/x"))
    assert code(lab.run()) == policy.UNSAFE_TARGET


def test_socket_path_of_the_helper_is_unknown(lab):
    lab.helper["Mounts"] = [m for m in lab.helper["Mounts"] if m["Destination"] != "/var/run/docker.sock"]
    result = lab.run()
    assert (code(result), result.detail) == (policy.UNSAFE_TARGET, "socket_unknown")


def test_rootless_socket_path(lab):
    socket_mount = next(m for m in lab.helper["Mounts"] if m["Destination"] == "/var/run/docker.sock")
    socket_mount["Source"] = "/run/user/1000/docker.sock"
    assert lab.run().ready
    lab.target["Mounts"].append(_bind("/run/user/1000", "/x"))
    assert code(lab.run()) == policy.UNSAFE_TARGET


@pytest.mark.parametrize("change", [
    lambda lab: lab.target.update(Mounts=[m for m in lab.target["Mounts"] if m["Destination"] != "/app/updater"]),
    lambda lab: next(m for m in lab.target["Mounts"] if m["Destination"] == "/app/updater").update(Name="other"),
    lambda lab: next(m for m in lab.target["Mounts"] if m["Destination"] == "/app/updater").update(Type="bind"),
    lambda lab: next(m for m in lab.target["Mounts"] if m["Destination"] == "/app/updater").update(
        Destination="/app/updater2"),
    lambda lab: lab.helper.update(Mounts=[m for m in lab.helper["Mounts"] if m["Destination"] != "/channel"]),
])
def test_channel_missing_in_target(lab, change):
    change(lab)
    assert code(lab.run()) == policy.CHANNEL_MISSING_IN_TARGET


def test_channel_as_bind_on_both_sides(lab):
    channel = next(m for m in lab.helper["Mounts"] if m["Destination"] == "/channel")
    channel.update(Type="bind", Name="", Source="/srv/updater")
    in_target = next(m for m in lab.target["Mounts"] if m["Destination"] == "/app/updater")
    in_target.update(Type="bind", Name="", Source="/srv/updater")
    assert lab.run().ready
    in_target["Source"] = "/srv/other"
    assert code(lab.run()) == policy.CHANNEL_MISSING_IN_TARGET


# ---------------------------------------------------------------------------
# Klonbarkeit und Umgebung
# ---------------------------------------------------------------------------


def test_auto_remove(lab):
    lab.target["HostConfig"]["AutoRemove"] = True
    assert code(lab.run()) == policy.AUTO_REMOVE


def test_custom_entrypoint(lab):
    lab.target["Config"]["Entrypoint"] = []
    assert code(lab.run()) == policy.CUSTOM_ENTRYPOINT


def test_unknown_field(lab):
    lab.target["HostConfig"]["SomethingNew"] = {"x": 1}
    result = lab.run()
    assert (code(result), result.detail) == (policy.UNKNOWN_FIELD, "something_new")


def test_unknown_restart_policy(lab):
    lab.target["HostConfig"]["RestartPolicy"] = {"Name": "sometimes", "MaximumRetryCount": 0}
    assert code(lab.run()) == policy.UNKNOWN_FIELD


@pytest.mark.parametrize("driver", ["macvlan", "ipvlan"])
def test_macvlan(lab, driver):
    network_id = next(iter(lab.target["NetworkSettings"]["Networks"].values()))["NetworkID"]
    lab.world.networks[network_id]["Driver"] = driver
    assert code(lab.run()) == policy.MACVLAN


@pytest.mark.parametrize("change", [
    lambda lab: lab.world.networks.clear(),
    lambda lab: lab.target["HostConfig"].update(NetworkMode="elsewhere"),
    lambda lab: next(iter(lab.target["NetworkSettings"]["Networks"].values())).update(NetworkID=""),
    lambda lab: lab.target["NetworkSettings"].update(Networks={}),
])
def test_network_unclear(lab, change):
    change(lab)
    assert code(lab.run()) == policy.NETWORK_UNCLEAR


def test_network_answer_with_other_id(lab):
    network_id = next(iter(lab.target["NetworkSettings"]["Networks"].values()))["NetworkID"]
    lab.routes.append(("GET", f"/networks/{network_id}", Response(body={"Id": "0" * 64, "Driver": "bridge"})))
    assert code(lab.run()) == policy.NETWORK_UNCLEAR


def sidecar_of(lab, **host):
    """Ein weiterer Container (kein Compose-Ziel), dessen HostConfig `host` setzt."""
    sidecar = copy.deepcopy(lab.helper)
    sidecar["Id"] = "d" * 64
    sidecar["Name"] = "/sidecar"
    sidecar["Config"]["Labels"]["com.docker.compose.service"] = "sidecar"
    sidecar["HostConfig"].update(host)
    return lab.world.add_container(sidecar)


def refs(lab):
    target_id = lab.target["Id"]
    return {"full": target_id, "name": lab.target["Name"][1:], "slash_name": lab.target["Name"],
            "short12": target_id[:12], "short8": target_id[:8], "short4": target_id[:4], "short1": target_id[:1]}


@pytest.mark.parametrize("field", ["NetworkMode", "PidMode", "IpcMode"])
@pytest.mark.parametrize("ref", ["full", "name", "slash_name", "short12", "short8", "short4", "short1"])
def test_dependent_containers(lab, field, ref):
    # Der Docker-Dienst loest `container:<ref>` ueber die volle ID, den Namen oder ein kurzes, eindeutiges
    # ID-Praefix auf; Netz, PID- und IPC-Namensraum haengen am Ziel.
    sidecar_of(lab, **{field: "container:" + refs(lab)[ref]})
    result = lab.run()
    assert code(result) == policy.DEPENDENT_CONTAINERS
    assert result.detail == {"NetworkMode": "network_mode", "PidMode": "pid_mode", "IpcMode": "ipc_mode"}[field]


@pytest.mark.parametrize("field", ["NetworkMode", "PidMode", "IpcMode"])
def test_dependent_stopped_container_counts_too(lab, field):
    sidecar_of(lab, **{field: "container:" + lab.target["Id"]})["State"].update(Status="exited", Running=False)
    assert code(lab.run()) == policy.DEPENDENT_CONTAINERS


def test_pid_mode_is_read_from_the_inspect_not_from_the_list(lab):
    # `GET /containers/json` liefert in `HostConfig` nur `NetworkMode`: ohne den Inspect bliebe PidMode unsichtbar.
    sidecar_of(lab, PidMode="container:" + lab.target["Id"])
    assert all(set(item["HostConfig"]) == {"NetworkMode"} for item in lab.world.list_containers({}))
    result = lab.run()
    assert code(result) == policy.DEPENDENT_CONTAINERS
    assert lab.fake.calls("GET", "/containers/" + "d" * 64 + "/json")


@pytest.mark.parametrize("value", [
    "container:" + "0" * 64, "container:other-name", "container:0123", "container:", "container:zz", "host", "private",
    "", "shareable", "container:" + "A" * 12,
])
@pytest.mark.parametrize("field", ["NetworkMode", "PidMode", "IpcMode"])
def test_other_container_modes_are_no_dependency(lab, field, value):
    assert not lab.target["Id"].startswith(value.removeprefix("container:")) or value in ("", "container:")
    sidecar_of(lab, **{field: value})
    assert lab.run().ready


def test_other_container_mode_of_the_helper_is_no_dependency(lab):
    lab.helper["HostConfig"]["NetworkMode"] = "container:" + "0" * 64
    assert lab.run().ready


@pytest.mark.parametrize("field", ["NetworkMode", "PidMode", "IpcMode"])
@pytest.mark.parametrize("ref", ["full", "name", "short12"])
def test_helper_pointing_at_the_target_is_a_dependency(lab, field, ref):
    # Der Helfer wird nicht uebersprungen: Mit `PidMode=container:<ziel>` stuerbe er beim Stoppen des Ziels mit und
    # liesse sich nicht neu starten, solange das Ziel steht. Konfigurationsfehler, fail-closed.
    lab.helper["HostConfig"][field] = "container:" + refs(lab)[ref]
    result = lab.run()
    assert code(result) == policy.DEPENDENT_CONTAINERS
    assert result.detail == {"NetworkMode": "network_mode", "PidMode": "pid_mode", "IpcMode": "ipc_mode"}[field]


def container_named(lab, name, container_id="e" * 64):
    other = copy.deepcopy(lab.helper)
    other["Id"] = container_id
    other["Name"] = "/" + name
    other["Config"]["Labels"] = {"x": "y"}
    return lab.world.add_container(other)


@pytest.mark.parametrize("field", ["NetworkMode", "PidMode", "IpcMode"])
@pytest.mark.parametrize("length", [1, 2, 4])
def test_hex_name_of_another_container_is_no_prefix_of_the_target(lab, field, length):
    # `container:db` meint den Container NAMENS `db`: der Docker-Dienst loest erst volle ID, dann Namen, erst dann ein
    # ID-Praefix auf. Beginnt die ID des Ziels zufaellig mit `db`, darf das den Sidecar nicht zum Nachbarn machen.
    short = lab.target["Id"][:length]
    container_named(lab, short)
    sidecar_of(lab, **{field: "container:" + short})
    assert lab.run().ready


@pytest.mark.parametrize("field", ["NetworkMode", "PidMode", "IpcMode"])
def test_hex_ref_without_a_container_of_that_name_is_still_a_prefix(lab, field):
    short = lab.target["Id"][:2]
    sidecar_of(lab, **{field: "container:" + short})
    assert code(lab.run()) == policy.DEPENDENT_CONTAINERS


def test_name_of_another_container_does_not_hide_the_real_reference(lab):
    # Ein Container namens wie ein ANDERES Praefix aendert nichts: der Verweis auf die volle ID bleibt ein Verweis.
    container_named(lab, lab.target["Id"][:2])
    sidecar_of(lab, PidMode="container:" + lab.target["Id"])
    assert code(lab.run()) == policy.DEPENDENT_CONTAINERS


def test_link_alias_is_no_name_of_another_container():
    # Link-Aliase (`/a/b`) in `Names` sind keine Containernamen und verdraengen kein Praefix.
    target_id = "db" + "0" * 62
    containers = [{"Id": target_id, "Names": ["/deck"]},
                  {"Id": "e" * 64, "Names": ["/other", "/other/db", "/third/ab", "nodb", 5]}]
    names = target._other_names(containers, target_id)
    assert names == {"other"}
    assert target.points_at("db", target_id=target_id, name="deck", other_names=names)
    assert not target.points_at("db", target_id=target_id, name="deck", other_names=names | {"db"})
    assert not target.points_at("/db", target_id=target_id, name="deck", other_names=names | {"db"})
    assert target.points_at("deck", target_id=target_id, name="deck", other_names=names | {"deck"})  # eigener Name zuerst


def test_container_that_vanishes_before_the_inspect_is_skipped(lab):
    sidecar_of(lab, PidMode="container:" + lab.target["Id"])
    lab.routes.append(("GET", "/containers/" + "d" * 64 + "/json", Response(status=404, body={"message": "gone"})))
    assert lab.run().ready


@pytest.mark.parametrize("answer", [Response(status=500, body={"message": "x"}), Response(body=[]),
                                    Response(body={"Id": "e" * 64, "HostConfig": {}}),
                                    Response(body={"Id": "d" * 64, "HostConfig": None})])
def test_unreadable_neighbour_means_not_ready(lab, answer):
    # Ohne die Pruefung gibt es kein "bereit" (fail-closed).
    sidecar_of(lab, PidMode="container:" + lab.target["Id"])
    lab.routes.append(("GET", "/containers/" + "d" * 64 + "/json", answer))
    result = lab.run()
    assert not result.ready and result.reason in (policy.ENGINE_UNREACHABLE, policy.ENGINE_UNSUPPORTED)


def test_name_taken(lab):
    other = copy.deepcopy(lab.helper)
    other["Id"] = "d" * 64
    other["Name"] = lab.target["Name"] + "-previous"
    other["Config"]["Labels"] = {"x": "y"}
    lab.world.add_container(other)
    assert code(lab.run()) == policy.NAME_TAKEN


def test_name_too_long_for_previous(lab):
    long_name = "/" + "n" * 125
    lab.target["Name"] = long_name
    assert code(lab.run()) == policy.NAME_TAKEN


def test_order_of_reasons(lab):
    # Ein selbst gebautes Image ohne Kanal meldet zuerst `foreign_image` (die Ursache), nicht den fehlenden Kanal.
    lab.target["Config"]["Image"] = "nodvard-deck:pi-abc"
    lab.target["Mounts"] = [m for m in lab.target["Mounts"] if m["Destination"] != "/app/updater"]
    lab.target["State"]["Health"]["Status"] = "unhealthy"
    assert code(lab.run()) == policy.FOREIGN_IMAGE
    lab.target["Config"]["Image"] = "ghcr.io/nodvard/deck"
    assert code(lab.run()) == policy.CHANNEL_MISSING_IN_TARGET


def test_preflight_never_raises_on_refusals(lab, monkeypatch):
    def boom(*args, **kwargs):
        raise Refusal(policy.NETWORK_UNCLEAR, "x")
    monkeypatch.setattr(target.clone, "build", boom)
    result = lab.run()
    assert (result.reason, result.detail) == (policy.NETWORK_UNCLEAR, "x")


@pytest.mark.parametrize("error", [KeyError("x"), TypeError("x"), AttributeError("x"), RuntimeError("x")])
def test_preflight_fails_closed_on_unexpected_errors(lab, monkeypatch, error):
    # Eine unverstandene Antwort (oder ein Fehler im Code) darf nie ein altes "bereit" stehen lassen.
    def boom(*args, **kwargs):
        raise error
    monkeypatch.setattr(target, "check_neighbours", boom)
    result = lab.run()
    assert (result.ready, result.reason, result.detail) == (False, policy.ENGINE_UNSUPPORTED, "unexpected")
    assert result.target is None and result.current_version == "0.7.0"


def test_invalid_service_name_is_a_programming_error(lab):
    with pytest.raises(ValueError):
        lab.run(service="Bad Service")


# ---------------------------------------------------------------------------
# Eigene ID aus /proc/self/mountinfo
# ---------------------------------------------------------------------------

ID = "5e756dd4de7b" + "0" * 52


def mountinfo(*lines: str) -> bytes:
    return "".join(line + "\n" for line in lines).encode()


def test_own_id_from_docker_mountinfo():
    data = mountinfo(
        "1 0 0:1 / / rw - overlay overlay rw",
        f"2 1 8:1 /var/lib/docker/containers/{ID}/resolv.conf /etc/resolv.conf rw - ext4 /dev/sda1 rw",
        f"3 1 8:1 /var/lib/docker/containers/{ID}/hostname /etc/hostname rw - ext4 /dev/sda1 rw",
        f"4 1 8:1 /var/lib/docker/containers/{ID}/hosts /etc/hosts rw - ext4 /dev/sda1 rw",
    )
    assert target.parse_mountinfo(data) == ID


@pytest.mark.parametrize("root", [
    f"/containers/{ID}/hostname",                                   # /var/lib/docker als eigenes Dateisystem
    f"/home/nico/.local/share/docker/containers/{ID}/hostname",     # rootless
    f"/data/docker/containers/{ID}/hostname",                       # anderes data-root
])
def test_own_id_from_other_roots(root):
    assert target.parse_mountinfo(mountinfo(f"3 1 8:1 {root} /etc/hostname rw - ext4 /dev/x rw")) == ID


@pytest.mark.parametrize("data", [
    b"",
    mountinfo("1 0 0:1 / / rw - overlay overlay rw"),
    mountinfo(f"3 1 8:1 /var/lib/docker/containers/{ID}/hostname /etc/other rw - ext4 /dev/x rw"),
    mountinfo(f"3 1 8:1 /var/lib/docker/containers/{ID}/x /etc/hostname rw - ext4 /dev/x rw"),
    mountinfo(f"3 1 8:1 /var/lib/docker/containers/{ID[:12]}/hostname /etc/hostname rw - ext4 /dev/x rw"),
    mountinfo(f"3 1 8:1 /var/lib/docker/containers/{ID.upper()}/hostname /etc/hostname rw - ext4 /dev/x rw"),
    mountinfo(f"3 1 8:1 /var/lib/docker/containers/{ID}/hostname /etc/hostname rw - ext4 /dev/x rw",
              f"4 1 8:1 /var/lib/docker/containers/{'1' * 64}/hosts /etc/hosts rw - ext4 /dev/x rw"),
    mountinfo(f"3 1 8:1 /var/lib/containers/storage/overlay-containers/{ID}/userdata/hostname /etc/hostname rw"),
    b"x" * (target.MOUNTINFO_MAX_BYTES + 1),
    "kaputt".encode("utf-16"),
])
def test_own_id_unknown(data):
    with pytest.raises(Refusal) as exc:
        target.parse_mountinfo(data)
    assert exc.value.code == policy.SELF_UNKNOWN


def test_read_mountinfo_of_this_process():
    data = target.read_mountinfo()
    assert isinstance(data, bytes) and b" / " in data
    try:
        found = target.own_container_id()
    except Refusal as exc:
        assert exc.code == policy.SELF_UNKNOWN  # ausserhalb eines Docker-Containers
    else:
        assert policy.is_container_id(found)


def test_host_path():
    mounts = [{"Destination": "/var/run/docker.sock", "Source": "/run/user/1000/docker.sock"},
              {"Destination": "/var", "Source": "/srv/var"}, {"Destination": "", "Source": "/x"}]
    assert target.host_path(mounts, "/var/run/docker.sock") == "/run/user/1000/docker.sock"
    assert target.host_path(mounts, "/var/lib/x") == "/srv/var/lib/x"
    assert target.host_path([{"Destination": "/", "Source": "/"}], "/var/run/docker.sock") == "/var/run/docker.sock"
    assert target.host_path([{"Destination": "/run", "Source": "/srv/run/"}], "/run") == "/srv/run/"
    assert target.host_path(mounts, "/etc") is None


def test_canon_and_covers():
    assert target._covers("/var/run", "/run/docker.sock")
    assert target._covers("/run/", "/var/run/docker.sock")
    assert target._covers("/", "/anything")
    assert not target._covers("/var/run2", "/var/run/docker.sock")
    assert not target._covers("/var/run/docker.sock.bak", "/var/run/docker.sock")


@pytest.mark.skipif(os.name != "posix", reason="nur Linux")
def test_selftest_mountinfo_line_matches_the_parser():
    from nodvard_deck_updater import __main__ as main
    assert main._selftest_mountinfo()
