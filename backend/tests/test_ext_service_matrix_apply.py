"""Image-Updates EINSPIELEN (service-matrix): erkennen, Befehle bauen, Ergebnis beurteilen.

Teil 1 (rein): `image_apply.py` ohne Host. Die Fluesse gegen ein Fake-Docker stehen weiter unten."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "extensions" / "service-matrix" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nodvard_sdk.types import Risk

from nodvard_deck_ext_service_matrix import image_apply as ia
from nodvard_deck_ext_service_matrix.image_apply import ComposeTarget, NotUpdatable

CID = "0123456789ab" + "0" * 52
IMG_ID = "sha256:" + "1" * 64


def labels():
    return {
        "com.docker.compose.project": "nextcloud",
        "com.docker.compose.service": "app",
        "com.docker.compose.project.working_dir": "/opt/nextcloud",
        "com.docker.compose.project.config_files": "/opt/nextcloud/docker-compose.yml",
        "com.docker.compose.oneoff": "False",
        "com.docker.compose.version": "2.29.1",
        "com.docker.compose.config-hash": "a" * 64,
    }


def info(lbls=None, **over):
    data = {
        "id": CID, "name": "/nextcloud-app", "image": "nextcloud:30-apache", "image_id": IMG_ID, "state": "running",
        "labels": labels() if lbls is None else lbls,
    }
    data.update(over)
    return data


L = lambda suffix: "com.docker.compose." + suffix


def target(**over) -> ComposeTarget:
    result = ia.classify(info(), own=False)
    assert isinstance(result, ComposeTarget), result
    fields = {**result.__dict__, **over}
    return ComposeTarget(**fields)


# --- classify -----------------------------------------------------------------------------


def test_a_normal_compose_container_is_updatable():
    t = ia.classify(info(), own=False)
    assert t == ComposeTarget(
        container="nextcloud-app", container_id=CID, image="nextcloud:30-apache", image_id=IMG_ID, project="nextcloud", service="app",
        working_dir="/opt/nextcloud", config_files=("/opt/nextcloud/docker-compose.yml",), env_files=(), config_hash="a" * 64,
    )


def test_relative_config_files_belong_to_the_project_directory_and_env_files_are_read():
    lbls = labels()
    lbls[L("project.config_files")] = "docker-compose.yml,./override.yml"
    lbls[L("project.environment_file")] = "/opt/nextcloud/prod.env"
    t = ia.classify(info(lbls), own=False)
    assert isinstance(t, ComposeTarget)
    assert t.config_files == ("/opt/nextcloud/docker-compose.yml", "/opt/nextcloud/override.yml")
    assert t.env_files == ("/opt/nextcloud/prod.env",)


@pytest.mark.parametrize(
    "case, kwargs, kind",
    [
        ("plain", {"lbls": {}}, "plain"),
        ("plain-null", {"lbls": None, "labels": None}, "plain"),
        ("swarm", {"lbls": {**labels(), "com.docker.swarm.service.name": "web"}}, "swarm"),
        ("stack", {"lbls": {"com.docker.stack.namespace": "prod"}}, "swarm"),
        ("oneoff", {"lbls": {**labels(), L("oneoff"): "True"}}, "oneoff"),
        ("portainer-dir", {"lbls": {**labels(), L("project.working_dir"): "/data/compose/7"}}, "portainer"),
        # Auch wenn die Dateien zufaellig auf dem Host lesbar sind: Portainer haelt die Variablen in seiner Datenbank.
        ("portainer-files", {"lbls": {**labels(), L("project.config_files"): "/data/compose/7/docker-compose.yml"}}, "portainer"),
        ("lattice-name", {"image": "lattice:latest"}, "self"),
        ("lattice-hub", {"image": "docker.io/library/lattice:1"}, "self"),
        ("lattice-previous", {"image": "lattice:previous"}, "self"),
        ("nodvard-deck-name", {"image": "nodvard-deck:latest"}, "self"),
        ("nodvard-deck-registry", {"image": "ghcr.io/x/nodvard-deck:1"}, "self"),
        ("nodvard-deck-previous", {"image": "nodvard-deck:previous"}, "self"),
        ("nodvard-typo", {"image": "nodvard:latest"}, "self"),
        ("nodvard-deck-ghcr", {"image": "ghcr.io/nodvard/deck:1.2"}, "self"),
        ("nodvard-deck-hub", {"image": "nodvard/deck:latest"}, "self"),
        ("nodvard-deck-own-registry", {"image": "registry.local:5000/nodvard/deck@sha256:" + "a" * 64}, "self"),
        ("nodvard-typo-registry", {"image": "ghcr.io/x/nodvard:2"}, "self"),
        ("v1-default-name", {"name": "/nextcloud_app_1", "lbls": {**labels(), L("version"): "1.29.2"}}, "compose_v1"),
    ],
)
def test_containers_that_get_no_button_say_why(case, kwargs, kind):
    kwargs = {**kwargs}
    lbls = kwargs.pop("lbls", "unset")
    data = info(**kwargs) if lbls == "unset" else info(lbls, **kwargs)
    result = ia.classify(data, own=False)
    assert isinstance(result, NotUpdatable) and result.kind == kind and result.why, case


def test_the_container_the_dashboard_runs_in_is_never_updated_here():
    result = ia.classify(info(), own=True)
    assert isinstance(result, NotUpdatable) and result.kind == "self" and "Deploy-Skript" in result.why


@pytest.mark.parametrize("image", ["ghcr.io/other/deck:1", "nodvard/deck-tools:1", "ghcr.io/nodvard/link:1", "deck:1", "ghcr.io/nodvard/decks:1"])
def test_only_nodvard_deck_is_the_dashboard_not_any_repo_called_deck(image):
    assert isinstance(ia.classify(info(image=image), own=False), ComposeTarget), image


def test_compose_v1_with_an_explicit_container_name_stays_updatable():
    lbls = {**labels(), L("version"): "1.29.2"}
    assert isinstance(ia.classify(info(lbls, name="/meine-app"), own=False), ComposeTarget)
    assert isinstance(ia.classify(info(lbls, name="/nextcloud_app_1"), own=False), NotUpdatable)
    # Compose v2 nennt Container auch `projekt-dienst-1` -- das ist kein v1.
    assert isinstance(ia.classify(info(name="/nextcloud-app-1"), own=False), ComposeTarget)


PROJECTS = ["x; rm -rf /", "X", "-p", "a b", "abc\n", "$(id)", "", "a" * 70]
SERVICES = ["-d", "a;b", "`id`", "a\nb", "", "a b", "$(id)", "a\n"]
WORKDIRS = ["relativ", "/a/../etc", "/a/$(reboot)", "/a;b", "/a\nb", "/a'b", '/a"b', "~/x", "/a|b", "/a&b", "/a*", "/a//b", "/a/./b", "", "/a\n"]
CONFIGS = ["/a.yml,/b$(id).yml", ",", "/a.yml,", ",/a.yml", ",".join(f"/f{i}.yml" for i in range(9)), "sub/../../etc/x.yml", "/a.yml\n", ""]
ENVS = ["/a.env,;", "/a b.env,/x$(id)", "rel/../x.env", ",".join(f"/e{i}.env" for i in range(9))]


@pytest.mark.parametrize(
    "key, value",
    [(L("project"), v) for v in PROJECTS if v]
    + [(L("service"), v) for v in SERVICES]
    + [(L("project.working_dir"), v) for v in WORKDIRS]
    + [(L("project.config_files"), v) for v in CONFIGS]
    + [(L("project.environment_file"), v) for v in ENVS],
)
def test_hostile_label_values_never_reach_a_command(key, value):
    lbls = {**labels(), key: value}
    if key == L("project.working_dir") and not value:
        lbls.pop(key)
    result = ia.classify(info(lbls), own=False)
    assert isinstance(result, NotUpdatable), (key, value)
    # `plain` (kein Projekt) und `portainer` gelten nur bei ihren eigenen Angaben; alles andere ist `labels`.
    assert result.kind == "labels", (key, value, result)


@pytest.mark.parametrize("field, value", [("name", "/web\n"), ("name", "/x;y"), ("image", "nginx; id"), ("image", "nginx\n"), ("image_id", "sha256:abc"), ("id", "xyz")])
def test_hostile_container_facts_are_blocked_too(field, value):
    result = ia.classify(info(**{field: value}), own=False)
    assert isinstance(result, NotUpdatable) and result.kind == "labels"


def test_builders_check_by_themselves_even_with_a_hand_built_bad_target():
    good = target()
    for bad in (
        target(project="x; rm -rf /"), target(service="a;b"), target(working_dir="/a b/$(id)"), target(config_files=("/a.yml\n",)),
        target(config_files=()), target(env_files=("/a'b",)), target(container="web\n"), target(image="nginx; id"), target(image_id="sha256:kurz"),
    ):
        with pytest.raises(ValueError):
            ia.compose_argv(bad, "pull", bad.service)
        with pytest.raises(ValueError):
            ia.apply_script(bad, old_image_id=IMG_ID, rollback="lattice-rollback/x:previous")
        with pytest.raises(ValueError):
            ia.display_command(bad)
        with pytest.raises(ValueError):
            ia.cmd_probe(bad)
        with pytest.raises(ValueError):
            ia.cmd_members(bad)
        with pytest.raises(ValueError):
            ia.cmd_verify(bad)
        with pytest.raises(ValueError):
            ia.cmd_config(bad)
    ia.check_target(good)
    for bad_name in ("x; id", "a b", "web\n", "-f"):
        with pytest.raises(ValueError):
            ia.cmd_apply_inspect([bad_name])
    with pytest.raises(ValueError):
        ia.apply_script(good, old_image_id="sha256:abc", rollback="lattice-rollback/x:previous")
    with pytest.raises(ValueError):
        ia.apply_script(good, old_image_id=IMG_ID, rollback="x; id")


# --- Befehle ------------------------------------------------------------------------------


def test_the_commands_reproduce_the_project_exactly():
    t = target()
    common = ["docker", "compose", "--ansi", "never", "-p", "nextcloud", "--project-directory", "/opt/nextcloud", "-f", "/opt/nextcloud/docker-compose.yml"]
    assert shlex.split(ia.pull_command(t)) == common + ["pull", "app"]
    assert shlex.split(ia.up_command(t)) == common + ["up", "-d", "--no-deps", "--no-build", "app"]
    assert shlex.split(ia.display_command(t)) == common + ["pull", "app", "&&"] + common + ["up", "-d", "--no-deps", "--no-build", "app"]
    assert "--wait" not in ia.display_command(t)


def test_several_files_env_files_and_paths_with_spaces_are_quoted_exactly():
    t = target(
        working_dir="/opt/mein stack", config_files=("/opt/mein stack/a.yml", "/opt/mein stack/b override.yml"), env_files=("/opt/mein stack/x.env",),
    )
    argv = shlex.split(ia.pull_command(t))
    assert argv[argv.index("--project-directory") + 1] == "/opt/mein stack"
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "-f"] == ["/opt/mein stack/a.yml", "/opt/mein stack/b override.yml"]
    assert argv[argv.index("--env-file") + 1] == "/opt/mein stack/x.env"
    assert argv[-2:] == ["pull", "app"]
    assert shlex.split(ia.cmd_config(t))[-3:] == ["config", "--format", "json"]
    assert shlex.split(ia.cmd_config_hash(t))[-3:] == ["config", "--hash", "app"]


def test_the_rollback_name_is_a_valid_image_name_and_never_shared_between_containers():
    from nodvard_deck_ext_service_matrix.image_updates import is_valid_ref

    assert ia.rollback_ref("My_App.1") == "lattice-rollback/my-app-1-dd7fff9a:previous"
    assert ia.rollback_ref("___").startswith("lattice-rollback/container-")
    for name in ("nextcloud-app", "My_App.1", "A" * 200, "a.b_c-d"):
        assert is_valid_ref(ia.rollback_ref(name))
    # Gleicher Name-Rumpf, verschiedene Container: verschiedene Sicherungen.
    refs = {ia.rollback_ref(n) for n in ("foo_bar", "foo-bar", "Foo.Bar", "foo.bar")}
    assert len(refs) == 4 and all(r.startswith("lattice-rollback/foo-bar-") for r in refs)


def test_the_script_pulls_then_tags_only_a_really_new_image_then_recreates():
    t = target()
    script = ia.apply_script(t, old_image_id=IMG_ID, rollback="lattice-rollback/nextcloud-app-902344c2:previous")
    assert script.index("@@step=pull") < script.index("@@step=tag") < script.index("@@step=up") < script.index("@@step=done")
    # Erst nach dem Pull, und nur wenn das gezogene Image eine andere ID hat als das laufende.
    tag = f"docker tag {IMG_ID} lattice-rollback/nextcloud-app-902344c2:previous"
    assert script.index(" pull app") < script.index("docker image inspect") < script.index(tag) < script.index("up -d --no-deps --no-build app")
    assert f'[ "$new" != {IMG_ID} ]' in script and '[ -n "$new" ]' in script
    assert f"|| exit {ia.RC_PULL}" in script and f"|| exit {ia.RC_UP}" in script
    assert script.count(" pull app") == 1 and script.count("up -d --no-deps --no-build app") == 1


def test_rollback_commands_go_back_to_the_old_image():
    lines = ia.rollback_commands(target(), "lattice-rollback/nextcloud-app-902344c2:previous").splitlines()
    assert lines[0] == "docker tag lattice-rollback/nextcloud-app-902344c2:previous nextcloud:30-apache"
    assert shlex.split(lines[1])[-5:] == ["up", "-d", "--no-deps", "--no-build", "app"]


def test_generated_commands_pass_the_deny_list():
    from nodvard_deck.core.deny_patterns import match_deny_patterns

    t = target()
    for cmd in (ia.display_command(t), ia.cmd_probe(t), ia.cmd_members(t), ia.cmd_verify(t), ia.cmd_config(t),
                ia.apply_script(t, old_image_id=IMG_ID, rollback="lattice-rollback/x:previous"), ia.cmd_apply_inspect(["web"])):
        assert match_deny_patterns(cmd) is None, cmd


def test_probe_command_quotes_every_path_and_reads_the_default_env_file_only_without_a_label():
    t = target(working_dir="/opt/a b", config_files=("/opt/a b/c.yml",))
    probe = ia.cmd_probe(t)
    assert "'/opt/a b'" in probe and "'/opt/a b/c.yml'" in probe and "'/opt/a b/.env'" in probe
    assert "@@envnoread" not in ia.cmd_probe(target(env_files=("/opt/nextcloud/x.env",)))


# --- Probe, Konfiguration, Mitglieder ---------------------------------------------------------


def probe_out(version="2.29.1", dir_=True, **files):
    lines = []
    if version:
        lines.append(f"@@compose={version}")
    if dir_:
        lines.append("@@dir")
    for state, path in files.items():
        lines.append(f"@@{state}={path}")
    return "\n".join(lines) + "\n"


def test_probe_results_name_the_problem():
    t = target()
    ok = probe_out(ok="/opt/nextcloud/docker-compose.yml")
    assert ia.parse_probe(ok, t) is None
    assert ia.parse_probe(probe_out(version="v5.5.1", ok="/x"), t) is None
    assert ia.parse_probe(probe_out(version=None), t).kind == "compose_missing"
    assert ia.parse_probe(probe_out(version="1.29.2"), t).kind == "compose_missing"
    assert ia.parse_probe(probe_out(dir_=False), t).why == "Projektordner /opt/nextcloud fehlt auf dem Host."
    assert ia.parse_probe(probe_out(missing="/opt/nextcloud/docker-compose.yml"), t).why == "Compose-Datei /opt/nextcloud/docker-compose.yml fehlt auf dem Host."
    assert ia.parse_probe(probe_out(noread="/opt/nextcloud/docker-compose.yml"), t).why.endswith("ist für den SSH-Benutzer nicht lesbar.")
    assert "Die .env-Datei im Projektordner" in ia.parse_probe(ok + "@@envnoread\n", t).why
    with_env = target(env_files=("/opt/nextcloud/x.env",))
    assert ia.parse_probe(probe_out(noread="/opt/nextcloud/x.env"), with_env).why.startswith("Umgebungsdatei")


CONFIG_OK = json.dumps({"services": {"app": {"image": "nextcloud:30-apache", "environment": {"PASSWORD": "GEHEIM"}}}})


@pytest.mark.parametrize(
    "stdout, stderr, code, kind",
    [
        (CONFIG_OK, "", 0, None),
        (CONFIG_OK, "WARN[0000] etwas Harmloses", 0, None),
        (json.dumps({"services": {"app": {"image": "docker.io/library/nextcloud:30-apache"}}}), "", 0, None),
        (json.dumps({"services": {"web": {"image": "x"}}}), "", 0, "service_missing"),
        ("kein json", "", 0, "config"),
        (json.dumps({"services": {"app": {"build": "."}}}), "", 0, "build_only"),
        (json.dumps({"services": {"app": {"image": "nextcloud:30-apache", "pull_policy": "never"}}}), "", 0, "pull_policy"),
        (json.dumps({"services": {"app": {"image": "nextcloud:31-apache"}}}), "", 0, "image_mismatch"),
        (json.dumps({"services": {"app": {"image": "nextcloud"}}}), "", 0, "image_mismatch"),  # latest != 30-apache
        (CONFIG_OK, 'WARN[0000] The "DB_PASS" variable is not set. Defaulting to a blank string.\nWARN[0000] The "TAG" variable is not set.', 0, "env_missing"),
        ("", "validating /opt/x.yml: services.app additional property foo is not allowed", 1, "config"),
    ],
)
def test_config_check(stdout, stderr, code, kind):
    result = ia.check_config(stdout, stderr, code, target())
    assert (result.kind if result else None) == kind
    if result:
        assert "GEHEIM" not in result.why


def test_missing_variables_are_listed_and_config_errors_show_the_first_line():
    why = ia.check_config("", 'WARN The "DB_PASS" variable is not set.\nWARN The "TAG" variable is not set.', 0, target()).why
    assert "(DB_PASS, TAG)" in why and "von Hand" in why
    assert ia.check_config("", "WARN irgendwas\nFehler eins\nFehler zwei", 1, target()).why.endswith(": Fehler eins")


def test_members_and_config_hash_parsing():
    assert ia.parse_members("nextcloud-app|running\nnextcloud-app-2|exited\nmuell\n") == [("nextcloud-app", "running"), ("nextcloud-app-2", "exited")]
    assert ia.parse_config_hash("app " + "b" * 64 + "\n", "app") == "b" * 64
    assert ia.parse_config_hash("other " + "b" * 64, "app") is None and ia.parse_config_hash("app zz", "app") is None
    assert "--filter" in ia.cmd_members(target()) and "label=com.docker.compose.oneoff=False" in ia.cmd_members(target())
    assert ia.parse_apply_inspect('muell\n{"id": "x", "labels": null}\n{kaputt\n')[0]["id"] == "x"


# --- Risiko, Warnungen, Texte ---------------------------------------------------------------


@pytest.mark.parametrize(
    "image, service, db",
    [
        ("postgres:16", "db", True), ("postgis/postgis:16-3.4", "x", True), ("mariadb:11", "x", True), ("bitnami/postgresql:16", "x", True),
        ("mongo:7", "x", True), ("influxdb:2", "x", True), ("timescale/timescaledb:latest-pg16", "x", True), ("nextcloud:30", "db", True),
        ("nextcloud:30", "app", False), ("nginx:1", "web", False), ("prometheuscommunity/postgres-exporter:v0", "exporter", False),
    ],
)
def test_databases_are_high_risk(image, service, db):
    assert ia.is_database_image(image, service) is db
    assert ia.risk_for(image, service) is (Risk.HIGH if db else Risk.MEDIUM)


def warn(**over):
    args = dict(image="nextcloud:30-apache", service="app", affected=["nextcloud-app"], stopped=[], drift=False, stale=False, registry_at=None)
    args.update(over)
    return ia.warnings_for(**args)


def test_warnings_are_specific_and_quiet_when_there_is_nothing_to_say():
    assert warn() == []
    assert any("Backup" in w for w in warn(image="postgres:16", service="db"))
    assert any("latest" in w for w in warn(image="nginx")) and any("latest" in w for w in warn(image="nginx:latest"))
    assert not any("latest" in w for w in warn(image="nginx:1.27"))
    assert any("DNS" in w for w in warn(image="pihole/pihole:2024.07"))
    assert any("Weiterleitungen" in w for w in warn(image="jc21/nginx-proxy-manager:2"))
    assert any("VPN" in w for w in warn(image="weejewel/wg-easy:14"))
    assert any("Portainer" in w for w in warn(image="portainer/portainer-ce:2"))
    assert any("Betrifft alle 2 Container des Dienstes „app“: a, b" in w for w in warn(affected=["a", "b"]))
    assert any("Gestoppte" in w and "b" in w for w in warn(stopped=["b"]))
    assert any("Compose-Datei wurde seit dem letzten Start geändert" in w for w in warn(drift=True))
    assert any("30.09.2026 06:31 UTC" in w for w in warn(stale=True, registry_at="2026-09-30T06:31:00+00:00"))


def test_reason_text_carries_the_essentials_for_the_actions_page():
    text = ia.reason_text("docker", "nextcloud-app", "nextcloud:30-apache", "nextcloud", "app", Risk.MEDIUM)
    for part in ("nextcloud-app", "docker", "nextcloud:30-apache", "„nextcloud“/„app“", "Kurz nicht erreichbar"):
        assert part in text
    assert "Backup" not in text
    assert "Datenbank – vorher Backup!" in ia.reason_text("docker", "db", "postgres:16", "p", "db", Risk.HIGH)


def test_plan_id_changes_with_every_input():
    base = ia.plan_id("cmd", IMG_ID, "sha256:" + "a" * 64)
    assert base == ia.plan_id("cmd", IMG_ID, "sha256:" + "a" * 64) and len(base) == 16
    assert len({base, ia.plan_id("cmd2", IMG_ID, "sha256:" + "a" * 64), ia.plan_id("cmd", "sha256:" + "2" * 64, "sha256:" + "a" * 64), ia.plan_id("cmd", IMG_ID, None)}) == 4


# --- Zustand nach dem Lauf ------------------------------------------------------------------

NEW_ID = "sha256:" + "9" * 64


def member(name="nextcloud-app", image_id=NEW_ID, status="running", exit_code=0, health=None, restarts=None, started=None):
    return ia.Member(name, image_id, status, exit_code, health, restarts, started)


def snap(*members, tag=NEW_ID):
    return ia.Snapshot(tuple(members), tag)


def test_verify_output_parsing():
    line = json.dumps({"name": "/nextcloud-app", "image_id": NEW_ID, "status": "running", "exit_code": 0, "health": "healthy"})
    got = ia.parse_verify(f"{line}\nmuell\n@@image={NEW_ID}\n")
    assert got == ia.Snapshot((ia.Member("nextcloud-app", NEW_ID, "running", 0, "healthy"),), NEW_ID)
    assert ia.parse_verify("@@image=\n") == ia.Snapshot((), None)
    assert ia.parse_verify(json.dumps({"name": "/x", "image_id": "i", "status": "exited", "exit_code": 137, "health": None}) + "\n").members[0].exit_code == 137


@pytest.mark.parametrize(
    "members, tag, final, ok, fragment",
    [
        ([member()], NEW_ID, False, True, "läuft wieder"),
        ([member(health="healthy")], NEW_ID, False, True, "(gesund)"),
        ([member(health="starting")], NEW_ID, False, None, ""),
        ([member(health="starting")], NEW_ID, True, True, "Healthcheck meldet noch „startet“"),
        ([member(health="unhealthy")], NEW_ID, False, False, "ungesund"),
        ([member(status="exited", exit_code=137)], NEW_ID, False, False, "Exit-Code 137"),
        ([member(status="dead", exit_code=None)], NEW_ID, False, False, "beendet"),
        ([member(status="restarting")], NEW_ID, False, None, ""),
        ([member(status="restarting")], NEW_ID, True, False, "immer wieder"),
        ([member(status="created")], NEW_ID, True, False, "created"),
        ([], NEW_ID, False, None, ""),
        ([], NEW_ID, True, False, "Kein Container"),
        # Gleiches Image nach dem Pull: Multi-Arch-Grenze, kein Fehler.
        ([member(image_id=IMG_ID)], IMG_ID, False, True, "Für diesen Host gibt es kein neueres Image"),
        # Neues Image geladen, Container nutzt es nicht.
        ([member(image_id=IMG_ID)], NEW_ID, False, False, "nutzt es aber nicht"),
        # Mehrere Replikate, eines noch alt.
        ([member("a", NEW_ID), member("b", IMG_ID)], NEW_ID, False, False, "nutzt es aber nicht"),
    ],
)
def test_the_verdict_after_the_run(members, tag, final, ok, fragment):
    verdict = ia.judge_verify(snap(*members, tag=tag), old_image_id=IMG_ID, final=final)
    if ok is None:
        assert verdict is None
        return
    assert verdict is not None and verdict.ok is ok and fragment in verdict.text
    if fragment.startswith("Für diesen Host"):
        assert verdict.changed is False
    if ok and fragment == "läuft wieder":
        assert verdict.changed and verdict.new_image_id == NEW_ID


def test_pull_failures_are_explained_and_keep_the_good_news():
    assert ia.describe_pull_failure("toomanyrequests: You have reached your pull rate limit").startswith("Abruflimit der Registry erreicht")
    assert "Kein Platz mehr auf dem Host" in ia.describe_pull_failure("write /var/lib/docker/x: no space left on device")
    assert "Anmeldedaten" in ia.describe_pull_failure("error getting credentials - err: exec: docker-credential-pass: executable file not found")
    other = ia.describe_pull_failure("@@step=pull\n Image x Pulling\nError response from daemon: kaputt")
    assert other.startswith("Herunterladen fehlgeschlagen: Error response from daemon: kaputt")
    for text in ("toomanyrequests", "no space left", "kaputt", ""):
        assert ia.describe_pull_failure(text).endswith("Der Container läuft unverändert weiter.")


def test_result_texts_contain_ids_rollback_and_a_log_tail():
    t = target()
    verdict = ia.Verdict(True, "Container läuft wieder (gesund).", new_image_id=NEW_ID, health="healthy")
    out = ia.success_output(t, verdict, old_image_id=IMG_ID, rollback="lattice-rollback/nextcloud-app-902344c2:previous", log="Recreated\n")
    lines = out.splitlines()
    assert lines[0] == "„nextcloud-app“ aktualisiert: nextcloud:30-apache"
    assert lines[1] == "Image-ID alt 111111111111 → neu 999999999999 · Container läuft wieder (gesund)."
    assert "Das alte Image bleibt als lattice-rollback/nextcloud-app-902344c2:previous erhalten." in out
    assert "  docker tag lattice-rollback/nextcloud-app-902344c2:previous nextcloud:30-apache" in out
    assert "„Ungenutzte Images entfernen“ löscht dieses Sicherungs-Image." in out
    assert out.endswith("--- Protokoll (Ende) ---\nRecreated")
    same = ia.success_output(t, ia.Verdict(True, "Kein neueres Image für diesen Host – Container unverändert (nur die Prüfsumme wurde nachgezogen).", changed=False),
                             old_image_id=IMG_ID, rollback="lattice-rollback/nextcloud-app-902344c2:previous", log="")
    assert "unverändert" in same.splitlines()[0] and "Zurück (auf dem Host)" not in same
    failed = ia.failure_output(t, "Neu erstellen fehlgeschlagen – der Dienst läuft womöglich nicht.", rollback=None, log="x", state="nextcloud-app: exited", show_rollback=True)
    assert "Aktueller Zustand: nextcloud-app: exited" in failed and "nicht als Sicherung markiert" in failed
    untouched = ia.failure_output(t, "Herunterladen fehlgeschlagen.", rollback="lattice-rollback/nextcloud-app-902344c2:previous", log="x")
    assert "Zurück" not in untouched and "Sicherung" not in untouched


# =============================================================================================
# Teil 2: gegen ein Fake-Docker (Hinweise beim Pruefen, Uebersicht, spaeter der Lauf)
# =============================================================================================

import asyncio  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
import hashlib  # noqa: E402

from nodvard_deck_ext_service_matrix import applier as ap  # noqa: E402
from nodvard_deck_ext_service_matrix import image_updates as iu  # noqa: E402
from nodvard_deck_ext_service_matrix.applier import ImageApplier  # noqa: E402
from nodvard_deck_ext_service_matrix.image_updates import ImageUpdateService  # noqa: E402


def dg(char: str) -> str:
    return "sha256:" + char * 64


NEW_ID = "sha256:" + "9" * 64
GEHEIM = "GEHEIM-PASSWORT-123"


class FakeResult:
    def __init__(self, exit_code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.exit_code, self.stdout, self.stderr, self.duration_ms = exit_code, stdout, stderr, 1


class FakeHost:
    def __init__(self, host_id: str = "h1", name: str = "docker") -> None:
        self.id, self.name, self.display_name, self.address, self.tags = host_id, name, name, "10.0.0.5", ["docker"]


class FakeApplyDocker:
    """Ein Docker-Host mit Compose. Liest die Befehle der Extension an ihren Merkmalen und
    antwortet mit Beispielausgaben; alles Unbekannte ist ein Fehler im Test."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.down: Exception | None = None
        self.infos: dict[str, dict] = {}
        self.images: dict[str, list[str]] = {IMG_ID: ["nextcloud@" + dg("a")], NEW_ID: ["nextcloud@" + dg("b")]}
        self.remote: dict[str, str] = {"nextcloud:30-apache": dg("b")}
        self.tag_image_id = IMG_ID
        self.compose_version: str | None = "2.29.1"
        self.dir_ok = True
        self.files = {"/opt/nextcloud/docker-compose.yml": "ok"}
        self.env_noread = False
        self.config_stdout = json.dumps({"services": {"app": {"image": "nextcloud:30-apache", "environment": {"PASSWORD": GEHEIM}}}})
        self.config_stderr = ""
        self.config_rc = 0
        self.hash_stdout = ""
        self.fail_apply_inspect = False
        self.add("nextcloud-app")

    def add(self, name: str, *, image: str = "nextcloud:30-apache", image_id: str = IMG_ID, lbls: dict | None = None, state: str = "running") -> dict:
        info_ = info(labels() if lbls is None else lbls, id=hashlib.sha256(name.encode()).hexdigest(), name="/" + name, image=image, image_id=image_id, state=state)
        self.infos[name] = info_
        return info_

    def running_ids(self) -> list[str]:
        return [i["id"][:12] for i in self.infos.values() if i["state"] == "running"]

    def resolve(self, arg: str) -> dict | None:
        for name, i in self.infos.items():
            if arg == name or i["id"].startswith(arg):
                return i
        return None

    def recreate_with_new_image(self, name: str = "nextcloud-app") -> None:
        """Wie nach einem erfolgreichen `up -d`."""
        self.infos[name]["image_id"] = NEW_ID
        self.infos[name]["state"] = "running"
        self.tag_image_id = NEW_ID

    def registry_calls(self) -> list[str]:
        return [c for c in self.calls if " imagetools " in c or " manifest " in c]

    def calls_with(self, marker: str) -> list[str]:
        return [c for c in self.calls if marker in c]

    def polls_made(self) -> list[str]:
        return [c for c in self.calls if c.startswith('d="$HOME"')]

    async def run(self, command: str) -> FakeResult:
        self.calls.append(command)
        if self.down:
            raise self.down
        hook = getattr(self, "handle_extra", None)
        if hook is not None:
            handled = await hook(command)
            if handled is not None:
                return handled
        argv = shlex.split(command)
        if command.startswith("docker container inspect --format '{\"id\""):
            if self.fail_apply_inspect:
                raise OSError("Verbindung weg")
            lines = []
            for arg in argv[5:]:
                i = self.resolve(arg)
                if i is not None and (arg == i["name"].lstrip("/") or i["id"].startswith(arg)):
                    lines.append(json.dumps({k: i[k] for k in ("id", "name", "image", "image_id", "state", "labels")}))
            return FakeResult(stdout="\n".join(lines) + "\n")
        if argv[:3] == ["docker", "ps", "-q"]:
            return FakeResult(stdout="".join(i + "\n" for i in self.running_ids()))
        if argv[:5] == ["docker", "container", "inspect", "--format", "{{.Name}}|{{.Config.Image}}|{{.Image}}"]:
            found = [self.resolve(a) for a in argv[5:]]
            return FakeResult(stdout="".join(f"{i['name']}|{i['image']}|{i['image_id']}\n" for i in found if i))
        if argv[:3] == ["docker", "image", "inspect"] and argv[-1] != "nextcloud:30-apache" and "{{.Id}}" not in command:
            digests = self.images.get(argv[-1])
            if digests is None:
                return FakeResult(1, "", "Error: No such image")
            return FakeResult(stdout=json.dumps({"repo_digests": digests, "id": argv[-1]}) + "\n")
        if argv[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            digest = self.remote.get(argv[4])
            return FakeResult(stdout=json.dumps(digest) + "\n") if digest else FakeResult(1, "", "not found")
        if command.startswith("v=$(docker compose version"):
            lines = [f"@@compose={self.compose_version}"] if self.compose_version else []
            lines += ["@@dir"] if self.dir_ok else []
            lines += [f"@@{state}={path}" for path, state in self.files.items()]
            lines += ["@@envnoread"] if self.env_noread else []
            return FakeResult(stdout="\n".join(lines) + "\n")
        if command.startswith("docker ps -a --filter"):
            main = self.infos["nextcloud-app"]["labels"]
            rows = [
                f"{n}|{i['state']}" for n, i in self.infos.items()
                if i["labels"].get(L("project")) == main.get(L("project")) and i["labels"].get(L("service")) == main.get(L("service"))
            ]
            return FakeResult(stdout="\n".join(rows) + "\n")
        if command.startswith("docker compose") and " config --format json" in command:
            return FakeResult(self.config_rc, self.config_stdout, self.config_stderr)
        if command.startswith("docker compose") and " config --hash " in command:
            return FakeResult(stdout=self.hash_stdout)
        return await self.run_run(command)

    async def run_run(self, command: str) -> FakeResult:
        raise AssertionError(f"Unerwarteter Befehl: {command}")


class Ctx:
    def __init__(self, tmp_path: Path, docker: FakeApplyDocker) -> None:
        self.data_dir = tmp_path
        self.docker = docker
        self.host = FakeHost()
        self.sent: list = []
        self.audit_rows: list[dict] = []
        outer = self

        class _Settings:
            async def get(self) -> dict:
                return {}

        class _Hosts:
            async def list(self, *, tag: str | None = None):
                return [outer.host]

            async def get(self, host_id: str):
                return outer.host if host_id == outer.host.id else None

        class _Exec:
            async def run(self, host, command: str, *, timeout_s: int = 60):
                return await outer.docker.run(command)

        class _Notify:
            async def send(self, notification, **_) -> None:
                outer.sent.append(notification)

        class _Audit:
            async def log(self, **kwargs) -> None:
                outer.audit_rows.append(kwargs)

        self.settings, self.hosts, self.exec, self.notify, self.audit = _Settings(), _Hosts(), _Exec(), _Notify(), _Audit()


def pretend_own_container(monkeypatch, short_id: str) -> None:
    """Nodvard Deck laeuft in diesem Container. Gesetzt wird dort, wo der Applier seine Funktion her hat:
    andere Tests laden die Extension neu, ein frisch importiertes `capabilities` waere ein anderes Modul."""
    monkeypatch.setitem(ap.is_own_container.__globals__, "_own_container_id", lambda: short_id)


def make_applier(tmp_path, docker: FakeApplyDocker | None = None):
    docker = docker or FakeApplyDocker()
    ctx = Ctx(tmp_path, docker)
    service = ImageUpdateService(ctx)  # type: ignore[arg-type]
    applier = ImageApplier(ctx, service)  # type: ignore[arg-type]
    service.apply_hints = applier.hints
    return service, applier, ctx


async def checked(tmp_path, docker: FakeApplyDocker | None = None):
    service, applier, ctx = make_applier(tmp_path, docker)
    await service.check_host(ctx.host)
    return service, applier, ctx


# --- Hinweise beim Pruefen ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_marks_which_update_containers_can_be_applied_with_one_extra_call(tmp_path):
    docker = FakeApplyDocker()
    docker.add("pihole", image="pihole/pihole:latest", image_id=dg("2"), lbls={})
    docker.images[dg("2")] = ["pihole/pihole@" + dg("c")]
    docker.remote["pihole/pihole:latest"] = dg("d")
    docker.add("nginx", image="nginx:1.27", image_id=dg("3"), lbls={**labels(), L("service"): "web"})
    docker.images[dg("3")] = ["nginx@" + dg("e")]
    docker.remote["nginx:1.27"] = dg("e")  # aktuell
    service, _, ctx = await checked(tmp_path, docker)

    snap = service.snapshot()["data"]
    assert snap["h1:nextcloud-app"]["apply"] == {"mode": "compose", "project": "nextcloud", "service": "app"}
    assert snap["h1:pihole"]["apply"]["mode"] == "none" and snap["h1:pihole"]["apply"]["kind"] == "plain"
    assert "Compose" in snap["h1:pihole"]["apply"]["why"]
    assert snap["h1:nginx"]["status"] == iu.CURRENT and "apply" not in snap["h1:nginx"]
    assert len(docker.calls_with('docker container inspect --format \'{"id"')) == 1
    inspected = shlex.split(docker.calls_with('docker container inspect --format \'{"id"')[0])[5:]
    assert sorted(inspected) == ["nextcloud-app", "pihole"]


@pytest.mark.asyncio
async def test_no_extra_call_without_updates_and_a_failing_hint_never_breaks_the_check(tmp_path):
    docker = FakeApplyDocker()
    docker.remote["nextcloud:30-apache"] = dg("a")  # aktuell
    service, _, _ = await checked(tmp_path, docker)
    assert service.snapshot()["data"]["h1:nextcloud-app"]["status"] == iu.CURRENT
    assert not docker.calls_with('{"id"')

    broken = FakeApplyDocker()
    broken.fail_apply_inspect = True
    service, _, _ = await checked(tmp_path, broken)
    result = service.snapshot()["data"]["h1:nextcloud-app"]
    assert result["status"] == iu.UPDATE and "apply" not in result
    assert service.snapshot()["hosts"]["h1"]["error"] is None


@pytest.mark.asyncio
async def test_recheck_merges_one_container_without_asking_the_registry_again(tmp_path):
    docker = FakeApplyDocker()
    docker.add("nginx", image="nginx:1.27", image_id=dg("3"), lbls={**labels(), L("service"): "web"})
    docker.images[dg("3")] = ["nginx@" + dg("e")]
    docker.remote["nginx:1.27"] = dg("e")
    service, _, ctx = await checked(tmp_path, docker)
    before = service.snapshot()
    assert before["data"]["h1:nextcloud-app"]["status"] == iu.UPDATE
    registry_before = len(docker.registry_calls())

    docker.recreate_with_new_image()
    result = await service.recheck_container(ctx.host, "nextcloud-app")

    after = service.snapshot()
    assert result is not None and result["status"] == iu.CURRENT and "apply" not in result
    assert after["data"]["h1:nextcloud-app"]["status"] == iu.CURRENT
    assert after["data"]["h1:nginx"] == before["data"]["h1:nginx"]
    assert after["hosts"]["h1"]["checked_at"] == before["hosts"]["h1"]["checked_at"]
    assert len(docker.registry_calls()) == registry_before, "gueltige Antworten der Registry werden wiederverwendet"
    assert not any("--force" in c for c in docker.calls)


@pytest.mark.asyncio
async def test_recheck_of_a_vanished_container_drops_its_entry_and_works_without_earlier_state(tmp_path):
    docker = FakeApplyDocker()
    service, _, ctx = await checked(tmp_path, docker)
    del docker.infos["nextcloud-app"]
    assert await service.recheck_container(ctx.host, "nextcloud-app") is None
    assert "h1:nextcloud-app" not in service.snapshot()["data"]

    fresh_docker = FakeApplyDocker()
    fresh_docker.recreate_with_new_image()
    _, _, fresh_ctx = make_applier(tmp_path, fresh_docker)
    fresh_service = ImageUpdateService(fresh_ctx)  # type: ignore[arg-type]
    fresh = await fresh_service.recheck_container(fresh_ctx.host, "nextcloud-app")
    assert fresh is not None and fresh["status"] == iu.CURRENT
    assert fresh_service.snapshot()["hosts"]["h1"]["checked_at"] is None


def test_the_named_inspect_builder_checks_names_itself():
    assert shlex.split(iu.cmd_inspect_named(["a", "b.c"]))[3:] == ["--format", "{{.Name}}|{{.Config.Image}}|{{.Image}}", "a", "b.c"]
    for bad in ("x; id", "a b", "web\n", "-f", ""):
        with pytest.raises(ValueError):
            iu.cmd_inspect_named([bad])


# --- Uebersicht (plan) ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_plan_lists_what_will_happen(tmp_path):
    docker = FakeApplyDocker()
    service, applier, ctx = await checked(tmp_path, docker)
    plan = await applier.plan(ctx.host, "nextcloud-app")
    assert isinstance(plan, ap.Plan)
    body = plan.as_response()
    assert body["ok"] is True and body["risk"] == "medium"
    assert (body["container"], body["image"], body["project"], body["service"], body["affected"]) == (
        "nextcloud-app", "nextcloud:30-apache", "nextcloud", "app", ["nextcloud-app"],
    )
    assert (body["current_short"], body["remote_short"], body["remote_digest"]) == ("111111111111", "bbbbbbbbbbbb", dg("b"))
    assert body["registry_at"] is not None and body["stale"] is False
    assert body["command"] == ia.display_command(target())
    assert body["rollback"].splitlines()[0] == "docker tag lattice-rollback/nextcloud-app-902344c2:previous nextcloud:30-apache"
    assert body["warnings"] == []
    assert body["plan_id"] == ia.plan_id(body["command"], IMG_ID, dg("b")) and plan.reason_text().startswith("Image-Update: „nextcloud-app“ auf docker")
    # Nur lesende Befehle: kein pull, kein up, kein Lauf auf dem Host.
    assert not any(w in c for c in docker.calls for w in (" pull", " up ", "@@launch", " restart", " stop", " rm "))


@pytest.mark.asyncio
async def test_the_plan_never_contains_environment_values(tmp_path):
    docker = FakeApplyDocker()
    service, applier, ctx = await checked(tmp_path, docker)
    plan = await applier.plan(ctx.host, "nextcloud-app")
    assert GEHEIM not in json.dumps(plan.as_response()) and GEHEIM not in json.dumps(applier.snapshot())
    docker.config_stderr = 'WARN The "X" variable is not set.'
    blocked = await applier.plan(ctx.host, "nextcloud-app")
    assert isinstance(blocked, NotUpdatable) and GEHEIM not in blocked.why


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setup, kind",
    [
        (lambda d: d.infos["nextcloud-app"].update(labels={**labels(), L("project.working_dir"): "/data/compose/7"}), "portainer"),
        (lambda d: d.infos["nextcloud-app"].update(labels={}), "plain"),
        (lambda d: d.infos["nextcloud-app"].update(labels={**labels(), "com.docker.swarm.service.name": "x"}), "swarm"),
        (lambda d: d.infos["nextcloud-app"].update(image="lattice:latest"), "self"),
        (lambda d: d.infos["nextcloud-app"].update(state="exited"), "not_running"),
    ],
)
async def test_blocked_containers_get_a_reason_and_no_further_calls(tmp_path, setup, kind):
    docker = FakeApplyDocker()
    service, applier, ctx = await checked(tmp_path, docker)
    setup(docker)
    docker.calls.clear()
    plan = await applier.plan(ctx.host, "nextcloud-app")
    assert isinstance(plan, NotUpdatable) and plan.kind == kind and plan.why
    assert len(docker.calls) == 1, "nach dem Inspect ist Schluss (kein Probe, keine Konfiguration)"


@pytest.mark.asyncio
async def test_the_own_container_and_stale_or_missing_checks_block_the_plan(tmp_path, monkeypatch):
    docker = FakeApplyDocker()
    service, applier, ctx = await checked(tmp_path, docker)
    own = docker.infos["nextcloud-app"]["id"][:12]
    pretend_own_container(monkeypatch, own)
    assert (await applier.plan(ctx.host, "nextcloud-app")).kind == "self"
    monkeypatch.undo()

    # Unbekannter Container / ungueltiger Name / kein "Update" laut letzter Pruefung.
    assert (await applier.plan(ctx.host, "gibt-es-nicht")).kind == "gone"
    assert (await applier.plan(ctx.host, "web\n")).kind == "name"
    docker.recreate_with_new_image()
    await service.recheck_container(ctx.host, "nextcloud-app")
    no_update = await applier.plan(ctx.host, "nextcloud-app")
    assert no_update.kind == "no_update" and "Image-Updates prüfen" in no_update.why
    service2, applier2, ctx2 = make_applier(tmp_path, FakeApplyDocker())
    assert (await applier2.plan(ctx2.host, "nextcloud-app")).kind == "no_update"  # noch nie geprueft


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setup, kind, fragment",
    [
        (lambda d: setattr(d, "compose_version", None), "compose_missing", "Compose v2"),
        (lambda d: setattr(d, "compose_version", "1.29.2"), "compose_missing", "Compose v2"),
        (lambda d: setattr(d, "dir_ok", False), "dir", "Projektordner /opt/nextcloud fehlt"),
        (lambda d: d.files.update({"/opt/nextcloud/docker-compose.yml": "missing"}), "files", "fehlt auf dem Host"),
        (lambda d: d.files.update({"/opt/nextcloud/docker-compose.yml": "noread"}), "files", "nicht lesbar"),
        (lambda d: setattr(d, "env_noread", True), "files", ".env-Datei"),
        (lambda d: setattr(d, "config_stderr", 'WARN The "DB_PASS" variable is not set.'), "env_missing", "DB_PASS"),
        (lambda d: setattr(d, "config_stdout", json.dumps({"services": {"app": {"image": "nextcloud:31"}}})), "image_mismatch", "anderes Image"),
        (lambda d: setattr(d, "config_stdout", json.dumps({"services": {"other": {"image": "x"}}})), "service_missing", "steht nicht"),
        (lambda d: (setattr(d, "config_rc", 1), setattr(d, "config_stdout", ""), setattr(d, "config_stderr", "yaml: line 3: mapping values")), "config", "yaml: line 3"),
    ],
)
async def test_the_plan_names_what_is_missing_on_the_host(tmp_path, setup, kind, fragment):
    docker = FakeApplyDocker()
    service, applier, ctx = await checked(tmp_path, docker)
    setup(docker)
    plan = await applier.plan(ctx.host, "nextcloud-app")
    assert isinstance(plan, NotUpdatable) and plan.kind == kind and fragment in plan.why, plan


@pytest.mark.asyncio
async def test_warnings_for_replicas_stopped_members_drift_and_databases(tmp_path):
    docker = FakeApplyDocker()
    docker.add("nextcloud-app-2", state="exited")
    docker.infos["nextcloud-app"]["labels"] = {**labels(), L("config-hash"): "a" * 64}
    docker.hash_stdout = "app " + "c" * 64 + "\n"
    service, applier, ctx = await checked(tmp_path, docker)
    plan = await applier.plan(ctx.host, "nextcloud-app")
    text = "\n".join(plan.warnings)
    assert "Betrifft alle 2 Container des Dienstes „app“" in text and "Gestoppte Container" in text and "Compose-Datei wurde seit dem letzten Start geändert" in text
    assert plan.affected == ("nextcloud-app", "nextcloud-app-2") and plan.risk is Risk.MEDIUM

    db = FakeApplyDocker()
    db.infos["nextcloud-app"].update(image="postgres:16")
    db.remote["postgres:16"] = dg("b")
    db.images[IMG_ID] = ["postgres@" + dg("a")]
    db.config_stdout = json.dumps({"services": {"app": {"image": "postgres:16"}}})
    service, applier, ctx = await checked(tmp_path, db)
    plan = await applier.plan(ctx.host, "nextcloud-app")
    assert plan.risk is Risk.HIGH and any("Backup" in w for w in plan.warnings) and plan.as_response()["risk"] == "high"
    assert "Datenbank – vorher Backup!" in plan.reason_text()


@pytest.mark.asyncio
async def test_a_running_update_of_the_same_project_blocks_the_plan_and_ssh_errors_come_out(tmp_path):
    docker = FakeApplyDocker()
    service, applier, ctx = await checked(tmp_path, docker)
    applier._busy["h1:nextcloud"] = "nextcloud-db"
    busy = await applier.plan(ctx.host, "nextcloud-app")
    assert busy.kind == "busy" and "schon ein Update" in busy.why
    applier._busy.clear()
    docker.down = OSError("Connection refused")
    with pytest.raises(OSError):
        await applier.plan(ctx.host, "nextcloud-app")


# =============================================================================================
# Teil 3: der Lauf (Gate-Aktion `container.image_update`) gegen das Fake-Docker
# =============================================================================================

from nodvard_sdk import REQUEST_WAIT_S, ActionRequest, Actor, Severity  # noqa: E402

from nodvard_deck_ext_service_matrix import detached as dt  # noqa: E402

RUN_ID = "imgupd_0123456789abcdef"

POLL_PULLING = "@@running\n@@step=pull\n@@tail\nnextcloud Pulling\n"
POLL_UP = "@@running\n@@step=up\n@@tail\nContainer nextcloud-app Recreate\n"
POLL_DONE = "@@rc=0\n@@step=done\n@@tail\n@@step=tag\nContainer nextcloud-app  Recreated\n"


class FlowDocker(FakeApplyDocker):
    """Dazu: der entkoppelte Lauf (Start, Abfragen) und die Pruefung nach dem Lauf."""

    def __init__(self) -> None:
        super().__init__()
        self.launched: list[str] = []
        self.launch_error: Exception | None = None
        self.launch_out = "@@launch\n@@method=setsid\n@@pid=4242\n"
        self.polls: list = [POLL_PULLING, POLL_UP, POLL_DONE]
        self.recreate_on_success = True
        self.verify_overrides: list[dict] = [{}]
        self.rollback_tag_id: str | None = IMG_ID
        self.poll_gate: asyncio.Event | None = None
        self.on_poll = None

    async def run_run(self, command: str) -> FakeResult:
        if command.startswith("echo @@launch"):
            self.launched.append(command)
            if self.launch_error:
                raise self.launch_error
            return FakeResult(stdout=self.launch_out)
        if command.startswith('d="$HOME"') and "@@running" in command:
            if self.on_poll:
                self.on_poll()
            if self.poll_gate is not None:
                await self.poll_gate.wait()
            item = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
            if isinstance(item, BaseException):
                raise item
            if "@@rc=0" in item and self.recreate_on_success:
                self.recreate_with_new_image()
            return FakeResult(stdout=item)
        if command.startswith("ids=$(docker ps -aq"):
            override = self.verify_overrides.pop(0) if len(self.verify_overrides) > 1 else self.verify_overrides[0]
            if isinstance(override, BaseException):
                raise override
            main = self.infos["nextcloud-app"]
            member = {"name": main["name"], "image_id": main["image_id"], "status": "running", "exit_code": 0, "health": None,
                      "restarts": 0, "started": "2026-09-30T07:00:00Z", **override}
            lines = [json.dumps(member), f"@@image={self.tag_image_id}", f"@@rollback={self.rollback_tag_id or ''}"]
            return FakeResult(stdout="\n".join(lines) + "\n")
        return await super().run_run(command)


@pytest.fixture
def fast(monkeypatch):
    for name in ("POLL_INTERVAL_S", "VERIFY_INTERVAL_S", "VERIFY_STABLE_S"):
        monkeypatch.setattr(ap, name, 0)


async def flow(tmp_path, docker: FlowDocker | None = None, *, correlation_id: str | None = RUN_ID):
    docker = docker or FlowDocker()
    service, applier, ctx = await checked(tmp_path, docker)
    plan = await applier.plan(ctx.host, "nextcloud-app")
    assert isinstance(plan, ap.Plan), plan
    return docker, service, applier, ctx, plan


def request_for(plan: ap.Plan, *, correlation_id: str | None = RUN_ID, **payload_over) -> ActionRequest:
    t = plan.target
    payload = {
        "container": t.container, "project": t.project, "service": t.service, "image": t.image, "old_image_id": t.image_id,
        "remote_digest": plan.remote_digest, "command": plan.command, **payload_over,
    }
    return ActionRequest(
        action_type="container.image_update", host_ref="h1", payload=payload, risk=plan.risk, proposed_by=Actor.user("u1", "owner1"),
        reason=plan.reason_text(), correlation_id=correlation_id,
    )


def no_launch(docker: FlowDocker) -> None:
    assert docker.launched == [] and not docker.calls_with("@@launch")


@pytest.mark.asyncio
async def test_success_pulls_recreates_verifies_rechecks_and_audits(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    phases: list[str] = []
    docker.on_poll = lambda: phases.append(next(iter(applier.snapshot()["applying"].values()))["phase"])
    result = await ap.ImageUpdateExecutor(applier).execute(request_for(plan))

    assert result.success is True and result.exit_code == 0 and result.error is None
    lines = result.output.splitlines()
    assert lines[0] == "„nextcloud-app“ aktualisiert: nextcloud:30-apache"
    assert lines[1] == "Image-ID alt 111111111111 → neu 999999999999 · Container läuft wieder."
    assert "Das alte Image bleibt als lattice-rollback/nextcloud-app-902344c2:previous erhalten." in result.output
    assert "docker tag lattice-rollback/nextcloud-app-902344c2:previous nextcloud:30-apache" in result.output
    assert "Container nextcloud-app  Recreated" in result.output
    assert result.detail["new_image_id"] == NEW_ID and result.detail["old_image_id"] == IMG_ID and result.detail["run_id"] == RUN_ID
    assert result.detail["log_path"] == f"~/.local/state/lattice-image-updates/{RUN_ID}.log"

    # Reihenfolge der Befehle: Uebersicht (lesend) -> Start -> Abfragen -> Pruefung -> Neubewertung.
    order = [c for c in docker.calls if c.startswith(("echo @@launch", 'd="$HOME"', "ids=$(docker ps -aq"))]
    assert order[0].startswith("echo @@launch") and order[-1].startswith("ids=$(docker ps -aq")
    assert all(c.startswith('d="$HOME"') for c in order[1:4]) and all(c.startswith("ids=") for c in order[4:])
    assert len(order) == 6, "Start, drei Abfragen, zwei ruhige Lesungen (ohne Healthcheck)"
    assert RUN_ID in docker.launched[0] and "docker tag" in docker.launched[0]
    # Das Skript steht zweimal im Start (setsid- und nohup-Zweig), gelaufen wird eines davon.
    assert docker.launched[0].count("--no-deps --no-build app") == 2 and docker.launched[0].count(" pull app") == 2
    assert phases == ["start", "pull", "up"]

    assert applier.snapshot()["applying"] == {}
    applied = applier.snapshot()["applied"]["h1:nextcloud-app"]
    assert applied["ok"] is True and applied["summary"].startswith("„nextcloud-app“ aktualisiert")
    assert service.snapshot()["data"]["h1:nextcloud-app"]["status"] == iu.CURRENT
    assert "h1:nextcloud" not in applier._busy and applier._busy == {}

    (row,) = ctx.audit_rows
    assert (row["action"], row["outcome"], row["target_type"], row["target_id"], row["correlation_id"]) == (
        "service_matrix.image_update", "success", "container", "h1:nextcloud-app", RUN_ID,
    )
    assert row["actor"].id == "u1" and row["detail"]["new_image_id"] == NEW_ID and row["detail"]["rollback_ref"] == "lattice-rollback/nextcloud-app-902344c2:previous"
    assert ctx.sent == [], "ein Erfolg unter einer Minute wird nicht gemeldet"


@pytest.mark.asyncio
async def test_a_long_success_is_pushed_without_a_host_id(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    ticks = iter(range(0, 10_000, 100))
    applier._now = lambda: float(next(ticks))
    result = await applier.apply(request_for(plan))
    assert result.success and result.duration_ms >= 100_000
    (note,) = ctx.sent
    assert note.severity is Severity.INFO and note.title == "Image-Update „nextcloud-app“ auf docker: fertig"
    assert note.payload == {"path": "/ext/service-matrix/matrix?host=h1", "tags": ["package"]}
    assert "host_id" not in note.payload and "host_ids" not in note.payload
    assert note.body.splitlines()[0] == "„nextcloud-app“ aktualisiert: nextcloud:30-apache"


@pytest.mark.asyncio
async def test_a_failing_pull_leaves_the_container_alone_and_always_pushes(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.polls = ["@@rc=10\n@@step=pull\n@@tail\nError response from daemon: toomanyrequests: You have reached your pull rate limit\n"]
    result = await applier.apply(request_for(plan))
    assert result.success is False and result.exit_code == ia.RC_PULL
    assert "Abruflimit" in result.error and "läuft unverändert weiter" in result.error
    assert "Zurück (auf dem Host)" not in result.output and "toomanyrequests" in result.output
    assert not docker.calls_with("ids=$(docker ps -aq"), "nach einem Pull-Fehler gibt es nichts zu pruefen"
    assert service.snapshot()["data"]["h1:nextcloud-app"]["status"] == iu.UPDATE
    assert applier.snapshot()["applied"]["h1:nextcloud-app"]["ok"] is False
    (note,) = ctx.sent
    assert note.severity is Severity.WARNING and note.title.endswith("fehlgeschlagen") and note.payload["tags"] == ["warning"]
    assert "Zurück" not in note.body
    assert ctx.audit_rows[0]["outcome"] == "failure"


@pytest.mark.asyncio
async def test_a_failing_recreate_shows_the_rollback_and_reads_the_state_exactly_once(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.polls = ["@@rc=20\n@@step=up\n@@tail\nError: port already allocated\n"]
    docker.verify_overrides = [{"status": "exited", "exit_code": 1}]
    result = await applier.apply(request_for(plan))
    assert result.success is False and result.exit_code == ia.RC_UP
    assert result.error.startswith("Neu erstellen fehlgeschlagen")
    assert "docker tag lattice-rollback/nextcloud-app-902344c2:previous nextcloud:30-apache" in result.output
    assert "Aktueller Zustand: nextcloud-app: exited" in result.output and "port already allocated" in result.output
    assert len(docker.calls_with("ids=$(docker ps -aq")) == 1
    (note,) = ctx.sent
    assert note.severity is Severity.WARNING and "Zurück (auf dem Host):\ndocker tag lattice-rollback/nextcloud-app-902344c2:previous" in note.body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override, ok, fragment",
    [
        ({"health": "unhealthy"}, False, "ungesund"),
        ({"status": "exited", "exit_code": 137}, False, "Exit-Code 137"),
        ({"status": "restarting"}, False, "immer wieder"),
        ({"health": "healthy"}, True, "(gesund)"),
    ],
)
async def test_the_start_is_checked_after_the_run(tmp_path, fast, override, ok, fragment, monkeypatch):
    monkeypatch.setattr(ap, "VERIFY_TIMEOUT_S", 0.05)
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.verify_overrides = [override]
    result = await applier.apply(request_for(plan))
    assert result.success is ok
    assert fragment in result.output
    if not ok:
        assert fragment in result.error and "Zurück (auf dem Host)" in result.output


@pytest.mark.asyncio
async def test_a_healthcheck_that_stays_on_starting_is_a_success_with_a_note(tmp_path, fast, monkeypatch):
    monkeypatch.setattr(ap, "VERIFY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(ap, "VERIFY_INTERVAL_S", 0.01)
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.verify_overrides = [{"health": "starting"}]
    result = await applier.apply(request_for(plan))
    assert result.success is True and "Healthcheck meldet noch „startet“" in result.output
    assert len(docker.calls_with("ids=$(docker ps -aq")) >= 2, "es wurde gewartet, nicht nur einmal gelesen"


@pytest.mark.asyncio
async def test_the_same_image_after_the_pull_is_a_success_that_says_nothing_changed(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.recreate_on_success = False  # Multi-Arch: die Registry hat etwas Neues, fuer diesen Host aber nicht
    result = await applier.apply(request_for(plan))
    assert result.success is True
    assert result.output.splitlines()[0] == "Für diesen Host gibt es kein neueres Image – der Container bleibt, wie er ist."
    assert "neu erstellt" not in result.output.split("--- Protokoll")[0] and "Zurück (auf dem Host)" not in result.output
    assert result.detail["recreated"] is False and result.detail["new_image_id"] == IMG_ID


@pytest.mark.asyncio
async def test_a_new_image_that_the_container_does_not_use_is_a_failure(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.recreate_on_success = False
    docker.tag_image_id = NEW_ID  # geladen, aber der Container blieb auf dem alten
    result = await applier.apply(request_for(plan))
    assert result.success is False and "nutzt es aber nicht" in result.error


@pytest.mark.asyncio
async def test_the_dashboard_container_is_never_launched(tmp_path, fast, monkeypatch):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    pretend_own_container(monkeypatch, docker.infos["nextcloud-app"]["id"][:12])
    docker.calls.clear()
    result = await applier.apply(request_for(plan))
    assert result.success is False and "Nodvard Deck selbst" in result.error
    no_launch(docker)
    assert not docker.calls_with("v=$(docker compose version")
    assert ctx.audit_rows == [] and ctx.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda d: d.infos["nextcloud-app"].update(labels={**labels(), L("project.working_dir"): "/data/compose/3"}), "Portainer"),
        (lambda d: d.infos["nextcloud-app"].update(labels={}), "Ohne Docker Compose"),
    ],
)
async def test_portainer_and_plain_containers_are_blocked_before_any_probe(tmp_path, fast, mutate, fragment):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    mutate(docker)
    docker.calls.clear()
    result = await applier.apply(request_for(plan))
    assert result.success is False and fragment in result.error
    no_launch(docker)
    assert not docker.calls_with("v=$(docker compose version")


@pytest.mark.asyncio
@pytest.mark.parametrize("over", [{"command": "docker compose down"}, {"old_image_id": "sha256:" + "7" * 64}, {"command": None}])
async def test_a_proposal_that_no_longer_fits_is_refused_without_a_launch(tmp_path, fast, over):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    result = await applier.apply(request_for(plan, **over))
    assert result.success is False and result.error == ap.STALE_MESSAGE
    no_launch(docker)


@pytest.mark.asyncio
async def test_the_executor_rebuilds_the_command_and_ignores_what_the_payload_would_run(tmp_path, fast):
    """`payload.command` wird nur verglichen. Sind die Angaben im Payload andere (anderer Container,
    schlechter Name), passiert nichts -- und in die Shell gelangt nie ein Payload-Text."""
    docker, service, applier, ctx, plan = await flow(tmp_path)
    for bad in ("x; rm -rf /", "web\n", "", "-f"):
        result = await applier.apply(request_for(plan, container=bad))
        assert result.success is False
    result = await applier.apply(request_for(plan, image="evil; id", project="p; id"))
    assert result.success is True  # nur `container` bestimmt, was gebaut wird; der Rest ist Anzeige
    assert not any("evil" in c or "p; id" in c for c in docker.calls)
    unknown_host = request_for(plan).model_copy(update={"host_ref": "gibt-es-nicht"})
    assert (await applier.apply(unknown_host)).error == "Host nicht gefunden."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda d: setattr(d, "compose_version", None), "Compose v2"),
        (lambda d: d.files.update({"/opt/nextcloud/docker-compose.yml": "noread"}), "nicht lesbar"),
        (lambda d: setattr(d, "env_noread", True), ".env-Datei"),
        (lambda d: setattr(d, "config_stdout", json.dumps({"services": {"app": {"image": "nextcloud:31"}}})), "anderes Image"),
        (lambda d: setattr(d, "config_stderr", 'WARN The "TAG" variable is not set.'), "TAG"),
    ],
)
async def test_what_is_wrong_on_the_host_is_named_and_nothing_starts(tmp_path, fast, mutate, fragment):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    mutate(docker)
    result = await applier.apply(request_for(plan))
    assert result.success is False and fragment in result.error
    no_launch(docker)


@pytest.mark.asyncio
async def test_a_second_update_of_the_same_project_is_refused_while_the_first_runs(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.poll_gate = asyncio.Event()
    first = asyncio.ensure_future(applier.apply(request_for(plan)))
    for _ in range(200):
        await asyncio.sleep(0)
        if "h1:nextcloud" in applier._busy:
            break
    assert applier._busy == {"h1:nextcloud": "nextcloud-app"}
    assert applier.snapshot()["applying"]["h1:nextcloud-app"]["run_id"] == RUN_ID
    second = await applier.apply(request_for(plan, correlation_id="imgupd_ffffffffffffffff"))
    assert second.success is False and second.error == ap.BUSY_MESSAGE
    assert len(docker.launched) == 1
    docker.poll_gate.set()
    assert (await first).success is True
    assert applier._busy == {} and applier.snapshot()["applying"] == {}


@pytest.mark.asyncio
async def test_connection_problems_while_polling_are_sat_out(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.polls = [OSError("Connection reset"), POLL_PULLING, "", OSError("weg"), "Connection lost\n", POLL_UP, POLL_DONE]
    result = await applier.apply(request_for(plan))
    assert result.success is True


@pytest.mark.asyncio
async def test_a_failing_launch_call_does_not_mean_failure_when_the_run_exists(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.launch_error = OSError("Kanal weg")
    result = await applier.apply(request_for(plan))
    assert result.success is True and len(docker.launched) == 1


@pytest.mark.asyncio
async def test_a_launch_that_never_reached_the_host_ends_with_that_reason(tmp_path, fast, monkeypatch):
    monkeypatch.setattr(ap, "REACH_GRACE_S", 0)
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.launch_error = OSError("Connection refused")
    docker.polls = [OSError("Connection refused")]
    result = await applier.apply(request_for(plan))
    assert result.success is False and result.error.startswith("Start fehlgeschlagen: Connection refused")
    assert applier._busy == {}


@pytest.mark.asyncio
async def test_a_launch_without_a_job_folder_says_not_started(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.launch_out = "@@launch\nmkdir: cannot create directory '/home/x/.local': Permission denied\n@@nopid\n"
    result = await applier.apply(request_for(plan))
    assert result.success is False and result.error == "Update-Lauf konnte nicht gestartet werden: mkdir: cannot create directory '/home/x/.local': Permission denied"
    assert not docker.polls_made(), "nichts gestartet: es wird auch nicht nachgefragt"


@pytest.mark.asyncio
async def test_an_unknown_run_lost_runs_and_silence_end_with_clear_messages(tmp_path, fast, monkeypatch):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    monkeypatch.setattr(ap, "UNKNOWN_GRACE_S", 0)
    docker.polls = ["@@unknown\n@@tail\n"]
    assert (await applier.apply(request_for(plan))).error == ap.NOT_STARTED
    docker.polls = ["@@lost\n@@step=pull\n@@tail\nletzte Zeile\n"]
    lost = await applier.apply(request_for(plan))
    assert "unerwartet beendet" in lost.error and dt.log_path(RUN_ID) in lost.error and "Zurück (auf dem Host)" not in lost.output
    docker.polls = ["@@lost\n@@step=up\n@@tail\nletzte Zeile\n"]
    lost_in_up = await applier.apply(request_for(plan))
    assert "Zurück (auf dem Host)" in lost_in_up.output, "ab dem Neuerstellen kann schon etwas veraendert sein"
    monkeypatch.setattr(ap, "RUN_DEADLINE_S", 0)
    docker.polls = [POLL_PULLING]
    silent = await applier.apply(request_for(plan))
    assert silent.error.startswith("Keine Rückmeldung – der Lauf kann auf dem Host weiterlaufen")
    assert applier._busy == {} and applier.snapshot()["applying"] == {}


@pytest.mark.asyncio
async def test_a_dashboard_shutdown_keeps_the_run_and_the_next_steps_possible(tmp_path, fast):
    """`CancelledError` (Dashboard faehrt herunter): der Lauf auf dem Host laeuft weiter, es wird
    nichts als fertig gemeldet, die Sperren werden frei. (Fortsetzen nach dem Neustart: Teil 3.)"""
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.poll_gate = asyncio.Event()
    task = asyncio.ensure_future(applier.apply(request_for(plan)))
    for _ in range(200):
        await asyncio.sleep(0)
        if docker.polls_made():
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert applier._busy == {} and applier.snapshot()["applying"] == {} and applier.snapshot()["applied"] == {}
    assert ctx.audit_rows == [] and ctx.sent == []
    assert not docker.calls_with("ids=$(docker ps -aq"), "nach dem Abbruch wird nichts mehr geprueft oder veraendert"


@pytest.mark.asyncio
async def test_the_run_id_comes_from_the_proposal_or_is_made_new(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    await applier.apply(request_for(plan, correlation_id=RUN_ID))
    assert RUN_ID in docker.launched[-1]
    for bad in (None, "x; rm -rf /", "imgupd_0123456789abcdeg", RUN_ID + "\n"):
        docker.polls = [POLL_PULLING, POLL_UP, POLL_DONE]
        docker.infos["nextcloud-app"]["image_id"] = IMG_ID
        docker.tag_image_id = IMG_ID
        await service.check_host(ctx.host)
        plan = await applier.plan(ctx.host, "nextcloud-app")
        assert isinstance(plan, ap.Plan)
        await applier.apply(request_for(plan, correlation_id=bad))
        used = docker.launched[-1]
        assert RUN_ID not in used and "x; rm" not in used
        import re

        assert re.search(r"imgupd_[0-9a-f]{16}", used)


@pytest.mark.asyncio
async def test_no_environment_value_reaches_result_snapshot_audit_or_push(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    assert GEHEIM in docker.config_stdout
    docker.polls = ["@@rc=20\n@@step=up\n@@tail\nError\n"]
    result = await applier.apply(request_for(plan))
    everything = json.dumps([result.model_dump(), applier.snapshot(), service.snapshot(), ctx.audit_rows, [n.model_dump() for n in ctx.sent]], default=str)
    assert GEHEIM not in everything
    assert not any(GEHEIM in c for c in docker.launched)


@pytest.mark.asyncio
async def test_propose_hands_the_gate_a_complete_request(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    seen: list = []

    class _Decision:
        action_id = "act-1"
        detail = None

        class status:  # noqa: N801
            value = "proposed"

    class _Actions:
        async def propose(self, request, *, wait_s=None):
            seen.append((request, wait_s))
            return _Decision()

    ctx.actions = _Actions()
    body = await applier.propose(ctx.host, plan, Actor.user("u1", "owner1"))
    assert body == {"action_id": "act-1", "status": "proposed", "risk": "medium", "detail": None}
    request, wait_s = seen[0]
    assert wait_s == REQUEST_WAIT_S
    assert (request.action_type, request.host_ref, request.risk) == ("container.image_update", "h1", Risk.MEDIUM)
    assert request.proposed_by.id == "u1" and dt.RUN_ID_RE.match(request.correlation_id)
    assert request.payload == {
        "container": "nextcloud-app", "project": "nextcloud", "service": "app", "image": "nextcloud:30-apache", "old_image_id": IMG_ID,
        "remote_digest": dg("b"), "command": plan.command,
    }
    assert request.reason == plan.reason_text() and "nextcloud-app" in request.reason and "nextcloud:30-apache" in request.reason


def test_the_spec_is_not_bound_to_hosts_and_the_gate_checks_the_command():
    spec = ap.IMAGE_UPDATE_SPEC
    assert spec.action_type == "container.image_update" and spec.host_bound is False and spec.command_field == "command"
    assert spec.default_risk is Risk.MEDIUM and spec.permissions == ["hosts.execute"]
    assert ap.ImageUpdateExecutor.action_types == {"container.image_update"}


# =============================================================================================
# Teil 4: Nachbesserungen aus der Gegenpruefung
# =============================================================================================


def test_only_two_calm_reads_count_as_started_without_a_healthcheck():
    calm = snap(member(restarts=0, started="t1"))
    assert ia.needs_stability_check(calm) is True
    assert ia.needs_stability_check(snap(member(health="healthy"))) is False
    assert ia.needs_stability_check(snap()) is False
    assert ia.restarted_since(calm, snap(member(restarts=0, started="t1"))) is False
    assert ia.restarted_since(calm, snap(member(restarts=1, started="t1"))) is True
    assert ia.restarted_since(calm, snap(member(restarts=0, started="t2"))) is True
    assert ia.restarted_since(calm, snap(member("anderer", restarts=0, started="t1"))) is True
    line = json.dumps({"name": "/x", "image_id": NEW_ID, "status": "running", "exit_code": 0, "health": None, "restarts": 3, "started": "t9"})
    parsed = ia.parse_verify(line + "\n").members[0]
    assert (parsed.restarts, parsed.started) == (3, "t9")
    assert '"restarts":{{json .RestartCount}}' in ia.VERIFY_FORMAT and '"started":{{json .State.StartedAt}}' in ia.VERIFY_FORMAT


@pytest.mark.asyncio
@pytest.mark.parametrize("second", [{"restarts": 1}, {"started": "2026-09-30T07:00:20Z"}, {"restarts": 2, "started": "x", "status": "restarting"}])
async def test_a_crash_loop_without_healthcheck_is_a_failure_even_though_the_first_read_says_running(tmp_path, fast, second):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.verify_overrides = [{}, second]  # erste Lesung: "running", zweite: neuer Start
    result = await applier.apply(request_for(plan))
    assert result.success is False and "startet immer wieder neu" in result.error
    assert "Zurück (auf dem Host)" in result.output
    assert len(docker.calls_with("ids=$(docker ps -aq")) >= 2


@pytest.mark.asyncio
async def test_success_without_healthcheck_needs_two_reads_and_waits_between_them(tmp_path, fast, monkeypatch):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    monkeypatch.setattr(ap, "VERIFY_STABLE_S", 0.05)
    monkeypatch.setattr(ap, "VERIFY_INTERVAL_S", 0.01)
    result = await applier.apply(request_for(plan))
    assert result.success is True
    assert len(docker.calls_with("ids=$(docker ps -aq")) >= 2, "ein einzelnes 'running' reicht ohne Healthcheck nicht"

    # Mit Healthcheck (gesund) genuegt eine Lesung.
    docker2, service2, applier2, ctx2, plan2 = await flow(tmp_path)
    docker2.verify_overrides = [{"health": "healthy"}]
    result2 = await applier2.apply(request_for(plan2))
    assert result2.success is True and len(docker2.calls_with("ids=$(docker ps -aq")) == 1


def test_the_dashboard_image_is_recognised_by_the_last_path_segment_too():
    for image in ("lattice:latest", "ghcr.io/nico/lattice:1.2", "nico/lattice", "registry.local:5000/team/x/lattice:dev"):
        result = ia.classify(info(image=image), own=False)
        assert isinstance(result, NotUpdatable) and result.kind == "self", image
    for image in ("ghcr.io/nico/lattice-tools:1", "nico/mylattice"):
        assert isinstance(ia.classify(info(image=image), own=False), ComposeTarget), image


def test_database_helpers_are_not_databases():
    for image in ("mongo-express:1", "mysql-ui:1", "ghcr.io/x/postgres-ui:1", "adminer", "postgres-admin:2", "prometheuscommunity/postgres-exporter:v0", "dpage/pgadmin4"):
        assert ia.is_database_image(image, "web") is False, image
    for image in ("mongo:7", "postgres:16", "mariadb:11", "mysql:8"):
        assert ia.is_database_image(image, "web") is True, image


def test_quoted_values_in_compose_errors_never_reach_the_text():
    stderr = 'WARN irgendwas\nerror while interpolating services.app.environment.PW: invalid interpolation format for "geheim$123" in \'auch$geheim\''
    why = ia.check_config("", stderr, 1, target()).why
    assert "geheim" not in why and why.endswith('in "…"') and "docker compose config meldet einen Fehler:" in why
    # Nachrichten ohne Anfuehrungszeichen bleiben lesbar.
    assert ia.check_config("", "yaml: line 3: mapping values", 1, target()).why.endswith("yaml: line 3: mapping values")


def test_success_output_for_an_unchanged_container_has_the_plain_sentence_and_no_recreate_line():
    t = target()
    verdict = ia.Verdict(True, ia.UNCHANGED_TEXT, changed=False)
    out = ia.success_output(t, verdict, old_image_id=IMG_ID, rollback="lattice-rollback/x:previous", log="Log\n")
    lines = out.splitlines()
    assert lines[0] == "Für diesen Host gibt es kein neueres Image – der Container bleibt, wie er ist."
    assert lines[1] == "--- Protokoll (Ende) ---" and lines[2] == "Log"
    assert "neu erstellt" not in out and "Image-ID" not in out and "Zurück" not in out


def test_probe_reports_a_running_update_of_the_project_from_the_host():
    t = target()
    assert "lock-nextcloud" in ia.cmd_probe(t) and "@@busy" in ia.cmd_probe(t)
    blocked = ia.parse_probe(probe_out(ok="/opt/nextcloud/docker-compose.yml") + "@@busy\n", t)
    assert blocked.kind == "busy" and blocked.why == "Für dieses Compose-Projekt läuft gerade schon ein Update."


@pytest.mark.asyncio
async def test_a_busy_host_lock_blocks_the_plan_and_a_busy_launch_is_reported(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.launch_out = "@@launch\n@@busy\n"
    result = await applier.apply(request_for(plan))
    assert result.success is False and result.error == "Für dieses Compose-Projekt läuft gerade schon ein Update."
    assert not docker.polls_made()
    assert "lock-nextcloud" in docker.launched[0], "die Sperre wird pro Compose-Projekt angelegt"


@pytest.mark.asyncio
async def test_the_rollback_is_not_promised_when_tagging_failed(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.polls = [POLL_PULLING, "@@rc=0\n@@step=done\n@@tail\n@@warn=rollback-tag\nRecreated\n"]
    result = await applier.apply(request_for(plan))
    assert result.success is True
    assert "Zurück (auf dem Host)" not in result.output and "lattice-rollback" not in result.output
    assert "nicht als Sicherung markiert" in result.output


@pytest.mark.asyncio
async def test_only_a_human_with_a_big_enough_risk_level_may_start_an_update(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    base = request_for(plan)
    for actor in (Actor.ai("modell"), Actor.extension("service-matrix"), Actor.scheduler("job")):
        refused = await applier.apply(base.model_copy(update={"proposed_by": actor}))
        assert refused.success is False and refused.error == ap.NOT_A_USER_MESSAGE
    refused = await applier.apply(base.model_copy(update={"risk": Risk.LOW}))
    assert refused.success is False and refused.error == ap.RISK_TOO_LOW_MESSAGE
    no_launch(docker)
    # Gleich hoch oder hoeher ist in Ordnung.
    assert (await applier.apply(base.model_copy(update={"risk": Risk.HIGH}))).success is True


@pytest.mark.asyncio
async def test_a_container_that_became_a_database_needs_the_higher_risk_again(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    assert plan.risk is Risk.MEDIUM
    docker.infos["nextcloud-app"]["image"] = "postgres:16"
    docker.images[IMG_ID] = ["postgres@" + dg("a")]
    docker.remote["postgres:16"] = dg("b")
    docker.config_stdout = json.dumps({"services": {"app": {"image": "postgres:16"}}})
    await service.check_host(ctx.host)
    result = await applier.apply(request_for(plan, image="postgres:16", command=ia.display_command(target(image="postgres:16"))))
    assert result.success is False and result.error == ap.RISK_TOO_LOW_MESSAGE
    no_launch(docker)


# =============================================================================================
# Teil 3: nach einem Neustart des Dashboards weitermachen (Schritt E)
# =============================================================================================

RESTART_LINE = "Das Dashboard wurde währenddessen neu gestartet."


def runs_file(ctx) -> Path:
    return ctx.data_dir / "image-apply-runs.json"


def stored_runs(ctx) -> dict:
    path = runs_file(ctx)
    return json.loads(path.read_text(encoding="utf-8"))["runs"] if path.exists() else {}


def new_dashboard(ctx):
    """Ein frisch gestartetes Dashboard: neuer Applier, derselbe Datenordner und derselbe Host."""
    service = ImageUpdateService(ctx)  # type: ignore[arg-type]
    applier = ImageApplier(ctx, service)  # type: ignore[arg-type]
    service.apply_hints = applier.hints
    return service, applier


async def wait_for_poll(docker: FlowDocker) -> None:
    for _ in range(500):
        await asyncio.sleep(0)
        if docker.polls_made():
            return
    raise AssertionError("es wurde nie nachgefragt")


async def interrupt_update(tmp_path):
    """Ein Update laeuft, das Dashboard wird beendet (CancelledError): der Eintrag bleibt."""
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.poll_gate = asyncio.Event()
    task = asyncio.ensure_future(applier.apply(request_for(plan)))
    await wait_for_poll(docker)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    docker.poll_gate = None
    docker.calls.clear()
    return docker, ctx, plan


def entry_from(plan: ap.Plan, **over) -> dict:
    t = plan.target
    return {
        "run_id": RUN_ID, "host_id": "h1", "container": t.container, "project": t.project, "service": t.service, "image": t.image,
        "old_image_id": t.image_id, "started_at": datetime.now(timezone.utc).isoformat(), "target": ap._target_to_json(t),
        "proposed_by": {"type": "user", "id": "u1", "label": "owner1"}, **over,
    }


@pytest.fixture(autouse=True)
def _no_active_runs_between_tests():
    ap._ACTIVE_RUNS.clear()
    yield
    ap._ACTIVE_RUNS.clear()


@pytest.fixture
def resume_fast(fast, monkeypatch):
    monkeypatch.setattr(ap, "RESUME_DELAY_S", 0)


@pytest.mark.asyncio
async def test_the_run_is_noted_before_the_launch_and_forgotten_after_a_normal_finish(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    seen: list[tuple[dict, str]] = []

    async def at_launch(command: str):
        if command.startswith("echo @@launch"):
            seen.append((stored_runs(ctx), runs_file(ctx).read_text(encoding="utf-8")))
        return None

    docker.handle_extra = at_launch
    result = await applier.apply(request_for(plan))
    assert result.success is True
    ((before_launch, raw),) = seen
    entry = before_launch[RUN_ID]
    assert entry["run_id"] == RUN_ID and entry["host_id"] == "h1" and entry["container"] == "nextcloud-app"
    assert (entry["project"], entry["service"], entry["image"]) == ("nextcloud", "app", "nextcloud:30-apache")
    assert entry["old_image_id"] == IMG_ID and entry["started_at"]
    assert "rollback_ref" not in entry, "das Sicherungs-Image wird beim Fortsetzen neu abgeleitet, nicht aus der Datei gelesen"
    assert entry["proposed_by"] == {"type": "user", "id": "u1", "label": "owner1"}
    assert GEHEIM not in raw
    assert stored_runs(ctx) == {}, "nach einem normalen Ende ist der Eintrag weg"


@pytest.mark.asyncio
async def test_a_failed_run_forgets_its_entry_too_and_other_entries_stay(tmp_path, fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    applier._store_run({"run_id": "imgupd_ffffffffffffffff", "host_id": "h9"})
    docker.polls = ["@@rc=10\n@@step=pull\n@@tail\nEs ging schief\n"]
    assert (await applier.apply(request_for(plan))).success is False
    assert list(stored_runs(ctx)) == ["imgupd_ffffffffffffffff"]
    # Auch ein Start, der gar nicht zustande kam, hinterlaesst nichts.
    docker.launch_out = "@@launch\n@@nopid\n"
    assert (await applier.apply(request_for(plan))).error.startswith("Update-Lauf konnte nicht gestartet werden")
    assert list(stored_runs(ctx)) == ["imgupd_ffffffffffffffff"]


@pytest.mark.asyncio
async def test_a_dashboard_shutdown_keeps_the_entry(tmp_path, fast):
    docker, ctx, plan = await interrupt_update(tmp_path)
    entry = stored_runs(ctx)[RUN_ID]
    assert entry["container"] == "nextcloud-app" and entry["host_id"] == "h1"


@pytest.mark.asyncio
async def test_after_the_restart_the_run_is_polled_verified_reported_and_forgotten(tmp_path, resume_fast):
    docker, ctx, plan = await interrupt_update(tmp_path)
    docker.polls = [POLL_UP, POLL_DONE]
    service, applier = new_dashboard(ctx)
    assert await applier.resume_interrupted() == 1

    assert not docker.calls_with("echo @@launch"), "nichts wird ein zweites Mal gestartet"
    assert len(docker.polls_made()) == 2 and docker.calls_with("ids=$(docker ps -aq")
    assert stored_runs(ctx) == {} and applier._busy == {} and applier.snapshot()["applying"] == {}
    applied = applier.snapshot()["applied"]["h1:nextcloud-app"]
    assert applied["ok"] is True and applied["summary"].startswith("„nextcloud-app“ aktualisiert")
    assert service.snapshot()["data"]["h1:nextcloud-app"]["status"] == iu.CURRENT, "der Stand der Seite wird neu bewertet"

    (row,) = ctx.audit_rows
    assert (row["action"], row["outcome"], row["target_id"], row["correlation_id"]) == ("service_matrix.image_update", "success", "h1:nextcloud-app", RUN_ID)
    assert row["detail"]["new_image_id"] == NEW_ID and row["detail"]["resumed"] is True
    (note,) = ctx.sent
    assert note.severity is Severity.INFO and note.title.endswith("fertig") and note.body.endswith(RESTART_LINE)
    assert "host_id" not in note.payload and note.payload["path"] == "/ext/service-matrix/matrix?host=h1"


@pytest.mark.asyncio
async def test_a_failure_after_the_restart_is_reported_with_the_restart_line_too(tmp_path, resume_fast):
    docker, ctx, plan = await interrupt_update(tmp_path)
    docker.polls = ["@@rc=10\n@@step=pull\n@@tail\nError response from daemon: toomanyrequests: You have reached your pull rate limit\n"]
    _service, applier = new_dashboard(ctx)
    await applier.resume_interrupted()
    (note,) = ctx.sent
    assert note.severity is Severity.WARNING and "Abruflimit" in note.body and note.body.endswith(RESTART_LINE)
    assert ctx.audit_rows[0]["outcome"] == "failure" and stored_runs(ctx) == {}


@pytest.mark.asyncio
async def test_after_the_restart_the_same_stability_rules_apply(tmp_path, resume_fast):
    docker, ctx, plan = await interrupt_update(tmp_path)
    docker.polls = [POLL_DONE]
    docker.verify_overrides = [{"restarts": 0}, {"restarts": 1, "started": "2026-09-30T07:05:00Z"}]  # Absturz-Kreislauf
    _service, applier = new_dashboard(ctx)
    await applier.resume_interrupted()
    (note,) = ctx.sent
    assert note.severity is Severity.WARNING and note.title.endswith("fehlgeschlagen") and note.body.endswith(RESTART_LINE)


@pytest.mark.asyncio
async def test_a_host_that_stays_silent_ends_with_the_timeout_message_at_the_deadline(tmp_path, resume_fast, monkeypatch):
    docker, ctx, plan = await interrupt_update(tmp_path)
    docker.down = OSError("Verbindung weg")
    monkeypatch.setattr(ap, "RUN_DEADLINE_S", 0)
    monkeypatch.setattr(ap, "RESUME_MIN_WAIT_S", 0)
    _service, applier = new_dashboard(ctx)
    await applier.resume_interrupted()
    assert len(docker.polls_made()) >= 1
    (note,) = ctx.sent
    assert note.severity is Severity.WARNING
    assert "Keine Rückmeldung – der Lauf kann auf dem Host weiterlaufen (Protokoll: ~/.local/state/lattice-image-updates/" + RUN_ID in note.body.splitlines()[0]
    assert note.body.endswith(RESTART_LINE)
    assert ctx.audit_rows[0]["outcome"] == "failure"
    assert stored_runs(ctx) == {} and applier._busy == {} and applier.snapshot()["applying"] == {}


@pytest.mark.asyncio
async def test_the_deadline_counts_from_the_start_but_is_at_least_five_minutes_from_now(tmp_path, resume_fast):
    docker, ctx, plan = await interrupt_update(tmp_path)
    waits: list[float] = []
    _service, applier = new_dashboard(ctx)
    real_wait = applier._wait

    async def spy(*args, deadline_s=None, **kwargs):
        waits.append(deadline_s)
        return await real_wait(*args, deadline_s=deadline_s, **kwargs)

    applier._wait = spy  # type: ignore[method-assign]
    docker.polls = [POLL_DONE]
    await applier.resume_interrupted()  # gerade erst gestartet: fast die ganze Frist ist noch da
    assert 0.9 * ap.RUN_DEADLINE_S < waits[-1] <= ap.RUN_DEADLINE_S
    # Kurz vor der Frist gestartet: trotzdem mindestens 5 Minuten ab jetzt.
    late = datetime.now(timezone.utc) - timedelta(seconds=ap.RUN_DEADLINE_S - 60)
    applier._store_run(entry_from(plan, started_at=late.isoformat()))
    docker.polls = [POLL_DONE]
    await applier.resume_interrupted()
    assert waits[-1] == ap.RESUME_MIN_WAIT_S == 5 * 60


@pytest.mark.parametrize("started", ["2999-01-01T00:00:00+00:00", "future-soon"])
@pytest.mark.asyncio
async def test_a_start_time_in_the_future_never_stretches_the_deadline(tmp_path, resume_fast, started):
    docker, ctx, plan = await interrupt_update(tmp_path)
    if started == "future-soon":
        started = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    applier = new_dashboard(ctx)[1]
    applier._store_run(entry_from(plan, started_at=started))
    waits: list[float] = []
    real_wait = applier._wait

    async def spy(*args, deadline_s=None, **kwargs):
        waits.append(deadline_s)
        assert applier.snapshot()["applying"]["h1:nextcloud-app"]["started_at"].startswith(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        return await real_wait(*args, deadline_s=deadline_s, **kwargs)

    applier._wait = spy  # type: ignore[method-assign]
    docker.polls = [POLL_DONE]
    await applier.resume_interrupted()
    assert waits and waits[0] <= ap.RUN_DEADLINE_S
    assert stored_runs(ctx) == {}


@pytest.mark.asyncio
async def test_the_rollback_image_is_derived_and_never_taken_from_the_file(tmp_path, resume_fast):
    docker, ctx, plan = await interrupt_update(tmp_path)
    runs = stored_runs(ctx)
    runs[RUN_ID]["rollback_ref"] = "x; rm -rf /"
    runs_file(ctx).write_text(json.dumps({"runs": runs}), encoding="utf-8")
    docker.polls = [POLL_DONE]
    await new_dashboard(ctx)[1].resume_interrupted()
    checks = docker.calls_with("ids=$(docker ps -aq")
    assert checks and all(plan.rollback_ref in c and "rm -rf" not in c for c in checks)
    assert ctx.audit_rows[0]["detail"]["rollback_ref"] == plan.rollback_ref


@pytest.mark.asyncio
async def test_a_failing_host_lookup_keeps_the_entry_for_the_next_start(tmp_path, resume_fast, caplog):
    docker, ctx, plan = await interrupt_update(tmp_path)
    applier = new_dashboard(ctx)[1]

    async def locked(host_id: str):
        raise RuntimeError("database is locked")

    real = ctx.hosts.get
    ctx.hosts.get = locked  # type: ignore[method-assign]
    with caplog.at_level("WARNING", logger="nodvard_deck.ext.service-matrix"):
        assert await applier.resume_interrupted() == 0
    assert RUN_ID in stored_runs(ctx) and applier._busy == {} and ctx.sent == []
    assert any("image_update_resume_host_lookup_failed" in r.getMessage() for r in caplog.records)
    ctx.hosts.get = real  # type: ignore[method-assign]
    docker.polls = [POLL_DONE]
    assert await applier.resume_interrupted() == 1
    assert stored_runs(ctx) == {}


@pytest.mark.asyncio
async def test_a_run_this_process_still_looks_after_is_not_resumed_a_second_time(tmp_path, resume_fast):
    """Extension aus- und wieder eingeschaltet: das alte `apply()` laeuft in der Gate-Aufgabe weiter."""
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.poll_gate = asyncio.Event()
    task = asyncio.ensure_future(applier.apply(request_for(plan)))
    await wait_for_poll(docker)
    _service2, applier2 = new_dashboard(ctx)
    assert await applier2.resume_interrupted() == 0
    assert RUN_ID in stored_runs(ctx), "der Eintrag bleibt fuer das laufende apply()"
    assert ctx.sent == [] and ctx.audit_rows == [] and applier2._busy == {}
    docker.poll_gate.set()
    result = await task
    assert result.success is True and len(ctx.audit_rows) == 1 and ctx.sent == []
    assert stored_runs(ctx) == {} and ap._ACTIVE_RUNS == set()


@pytest.mark.asyncio
async def test_a_resume_in_progress_is_not_taken_up_by_a_second_start_either(tmp_path, resume_fast):
    docker, ctx, plan = await interrupt_update(tmp_path)
    docker.poll_gate = asyncio.Event()
    first = asyncio.ensure_future(new_dashboard(ctx)[1].resume_interrupted())
    await wait_for_poll(docker)
    assert await new_dashboard(ctx)[1].resume_interrupted() == 0
    docker.poll_gate.set()
    assert await first == 1
    assert len(ctx.audit_rows) == 1 and len(ctx.sent) == 1


@pytest.mark.asyncio
async def test_the_audit_after_the_restart_names_who_approved_and_ignores_a_bad_actor(tmp_path, resume_fast):
    docker, ctx, plan = await interrupt_update(tmp_path)
    docker.polls = [POLL_DONE]
    await new_dashboard(ctx)[1].resume_interrupted()
    assert ctx.audit_rows[0]["actor"].id == "u1" and ctx.audit_rows[0]["actor"].type.value == "user"

    for bad in ({"type": "root", "id": "u1"}, {"type": "user", "id": ""}, {"type": "user", "id": "x" * 500}, {"type": "user", "id": "u", "label": 5}, "u1"):
        ctx.audit_rows.clear()
        ap._ACTIVE_RUNS.clear()
        applier = new_dashboard(ctx)[1]
        applier._store_run({**entry_from(plan), "proposed_by": bad})
        docker.polls = [POLL_DONE]
        await applier.resume_interrupted()
        assert ctx.audit_rows[0]["actor"] is None, bad


@pytest.mark.asyncio
async def test_entries_of_unknown_hosts_or_with_broken_content_are_dropped_with_a_log_line(tmp_path, resume_fast, caplog):
    docker, ctx, plan = await interrupt_update(tmp_path)
    good = stored_runs(ctx)[RUN_ID]
    runs = {
        "imgupd_1111111111111111": {**good, "run_id": "imgupd_1111111111111111", "host_id": "weg"},
        "imgupd_2222222222222222": {**good, "run_id": "imgupd_2222222222222222", "target": {"container": "x; id"}},
        "imgupd_3333333333333333": "kein Eintrag",
        "kaputt": {**good, "run_id": "kaputt"},
        "imgupd_4444444444444444": {**good, "run_id": "imgupd_4444444444444444", "target": {**good["target"], "config_files": "/opt/nextcloud/docker-compose.yml"}},
        "imgupd_5555555555555555": {**good, "run_id": "imgupd_5555555555555555", "target": {**good["target"], "env_files": "/opt/x.env"}},
        "imgupd_6666666666666666": {**good, "run_id": "imgupd_6666666666666666", "started_at": "gestern"},
        "imgupd_7777777777777777": {**good, "run_id": "imgupd_7777777777777777", "started_at": "2026-01-01T00:00:00+00:00"},
        "imgupd_8888888888888888": {**good, "run_id": "imgupd_8888888888888888", "started_at": None},
    }
    runs_file(ctx).write_text(json.dumps({"runs": runs}), encoding="utf-8")
    docker.calls.clear()
    _service, applier = new_dashboard(ctx)
    with caplog.at_level("WARNING", logger="nodvard_deck.ext.service-matrix"):
        assert await applier.resume_interrupted() == 0
    assert stored_runs(ctx) == {} and docker.calls == [] and ctx.sent == [] and ctx.audit_rows == []
    dropped = [r.getMessage() for r in caplog.records if "image_update_resume_dropped" in r.getMessage()]
    assert len(dropped) == 9 and any("existiert nicht mehr" in m for m in dropped) and any("zu alt" in m for m in dropped)
    assert applier._busy == {} and applier.snapshot()["applying"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["{kaputt", "[1, 2]", '{"runs": [1]}', "", "null"])
async def test_a_broken_file_is_ignored_with_a_log_line_and_the_next_update_replaces_it(tmp_path, resume_fast, caplog, content):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    runs_file(ctx).write_text(content, encoding="utf-8")
    with caplog.at_level("WARNING", logger="nodvard_deck.ext.service-matrix"):
        assert await applier.resume_interrupted() == 0
    assert any("image_update_runs_broken" in r.getMessage() for r in caplog.records)
    assert ctx.sent == [] and docker.calls_with("echo @@launch") == []
    assert (await applier.apply(request_for(plan))).success is True
    assert stored_runs(ctx) == {}


@pytest.mark.asyncio
async def test_while_resuming_the_container_counts_as_applying_and_the_project_is_locked(tmp_path, resume_fast):
    docker, ctx, plan = await interrupt_update(tmp_path)
    service, applier = new_dashboard(ctx)
    await service.check_host(ctx.host)  # die Seite kennt das Update noch: nur die Sperre haelt ein zweites auf
    docker.poll_gate = asyncio.Event()
    task = asyncio.ensure_future(applier.resume_interrupted())
    await wait_for_poll(docker)

    applying = applier.snapshot()["applying"]["h1:nextcloud-app"]
    assert applying["run_id"] == RUN_ID and applying["phase"] in {"start", "pull", "up"}
    assert applier._busy == {"h1:nextcloud": "nextcloud-app"}
    blocked = await applier.plan(ctx.host, "nextcloud-app")
    assert isinstance(blocked, NotUpdatable) and blocked.kind == "busy"
    second = await applier.apply(request_for(plan, correlation_id="imgupd_aaaaaaaaaaaaaaaa"))
    assert second.success is False and second.error == blocked.why
    assert not docker.calls_with("echo @@launch")

    docker.poll_gate.set()
    await task
    assert applier._busy == {} and applier.snapshot()["applying"] == {} and stored_runs(ctx) == {}


@pytest.mark.asyncio
async def test_a_second_shutdown_while_resuming_keeps_the_entry_and_frees_the_locks(tmp_path, resume_fast):
    docker, ctx, plan = await interrupt_update(tmp_path)
    docker.poll_gate = asyncio.Event()
    _service, applier = new_dashboard(ctx)
    task = asyncio.ensure_future(applier.resume_interrupted())
    await wait_for_poll(docker)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert RUN_ID in stored_runs(ctx) and applier._busy == {} and applier.snapshot()["applying"] == {}
    assert ctx.sent == [] and ctx.audit_rows == []


@pytest.mark.asyncio
async def test_without_entries_nothing_happens(tmp_path, resume_fast):
    docker, service, applier, ctx, plan = await flow(tmp_path)
    docker.calls.clear()
    assert await applier.resume_interrupted() == 0
    assert docker.calls == [] and not runs_file(ctx).exists()


@pytest.mark.asyncio
async def test_on_start_resumes_in_the_background_without_restarting_the_task(monkeypatch):
    import nodvard_deck_ext_service_matrix as pkg

    spawned: list[tuple] = []
    calls: list[str] = []

    async def resume() -> int:
        calls.append("resume")
        return 0

    class _Settings:
        async def get(self) -> dict:
            return {}

    class _Ctx:
        settings = _Settings()

        def spawn(self, coro, *, name, restart=True, restart_delay_s=5.0):
            spawned.append((coro, name, restart))

    async def no_job(*_a, **_k) -> None:
        return None

    monkeypatch.setattr(pkg, "_apply_docker_tag", lambda *_a: None)
    monkeypatch.setattr(pkg, "register_job", no_job)
    ext = pkg.Extension()
    ext._images = object()  # type: ignore[attr-defined]
    ext._applier = type("A", (), {"resume_interrupted": staticmethod(resume)})()  # type: ignore[attr-defined]
    await ext.on_start(_Ctx())  # type: ignore[arg-type]
    ((coro, name, restart),) = spawned
    assert name == "service-matrix-image-update-resume" and restart is False
    await coro
    assert calls == ["resume"]


@pytest.mark.asyncio
async def test_an_entry_whose_project_is_busy_is_dropped_the_running_update_reports_itself(tmp_path, resume_fast, caplog):
    docker, ctx, plan = await interrupt_update(tmp_path)
    applier = new_dashboard(ctx)[1]
    applier._busy["h1:nextcloud"] = "anderer-container"
    with caplog.at_level("WARNING", logger="nodvard_deck.ext.service-matrix"):
        assert await applier.resume_interrupted() == 0
    assert stored_runs(ctx) == {} and any("Projekt ist belegt" in r.getMessage() for r in caplog.records)
    assert applier._busy == {"h1:nextcloud": "anderer-container"} and ctx.sent == [] and ctx.audit_rows == []
