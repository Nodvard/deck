"""Update-Helfer: Compose-Datei `deploy/compose.standalone-mit-helfer.yml`, Image `deploy/updater/Dockerfile` und
der CI-Job, der es baut und startet.

Der Helfer bekommt den Docker-Socket und darf damit so viel wie root auf dem Rechner. Deshalb haelt dieser Test die
Schutzoptionen fest (kein Netz, nur lesbar, keine Capabilities, Grenzen) und dass die Datei sonst genau
`compose.standalone.yml` entspricht: gleicher Projektname, gleiche Daten, das Dashboard nur mit einer Zeile mehr (Kanal).
Die YAML-Struktur laeuft ueberall; `docker compose config` nur, wenn `docker compose` da ist.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
HELPER_COMPOSE = DEPLOY / "compose.standalone-mit-helfer.yml"
STANDALONE = DEPLOY / "compose.standalone.yml"
MAIN_COMPOSE = DEPLOY / "docker-compose.yml"
HELPER_DOCKERFILE = DEPLOY / "updater" / "Dockerfile"
DASHBOARD_DOCKERFILE = DEPLOY / "Dockerfile"
PACKAGE_DIR = DEPLOY / "updater" / "nodvard_deck_updater"
WORKFLOWS = ROOT / ".github" / "workflows"

CHANNEL_LINE = "nodvard_deck_updater:/app/updater"
SOCKET_LINE = "${NODVARD_DECK_DOCKER_SOCKET:-/var/run/docker.sock}:/var/run/docker.sock"
SELFTEST_FLAGS = ("--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges")
ENTRYPOINT = ["python", "-I", "-m", "nodvard_deck_updater"]


def _load(path: Path) -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _helper() -> dict:
    return _load(HELPER_COMPOSE)


def _updater() -> dict:
    return _helper()["services"]["updater"]


# ---------------------------------------------------------------------------
# Compose: Dashboard-Dienst gleich, Projekt und Daten gleich
# ---------------------------------------------------------------------------


def test_only_the_dashboard_and_the_helper():
    assert list(_helper()["services"]) == ["nodvard-deck", "updater"]


def test_dashboard_service_is_the_standalone_one_plus_the_channel():
    """Gleichlauf: Wer von compose.standalone.yml auf diese Datei wechselt, bekommt dasselbe Dashboard (Image, Port,
    Daten, Healthcheck, Logs) und nur zusaetzlich den Kanal unter /app/updater."""
    standalone = _load(STANDALONE)["services"]["nodvard-deck"]
    helper = _helper()["services"]["nodvard-deck"]
    assert helper["volumes"] == [*standalone["volumes"], CHANNEL_LINE]
    assert {k: v for k, v in helper.items() if k != "volumes"} == {k: v for k, v in standalone.items() if k != "volumes"}


def _service_block(path: Path, name: str) -> list[str]:
    """Die Zeilen eines Dienstes samt Kommentaren (bis zum naechsten Dienst bzw. Abschnitt)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"  {name}:")
    block = [lines[start]]
    for line in lines[start + 1:]:
        if line and not line.startswith("    "):
            break
        block.append(line)
    while block and not block[-1].strip():
        block.pop()
    return block


def test_dashboard_service_text_matches_the_standalone_file_line_by_line():
    """Auch die Erklaerungen bleiben gleich: nur die zwei Zeilen zum Kanal (Kommentar und Eintrag) kommen dazu."""
    standalone = _service_block(STANDALONE, "nodvard-deck")
    helper = _service_block(HELPER_COMPOSE, "nodvard-deck")
    added = [line for line in helper if line not in standalone]
    assert len(added) == 2 and added[1].strip() == f"- {CHANNEL_LINE}" and added[0].strip().startswith("#"), added
    assert [line for line in helper if line not in added] == standalone


def test_same_project_and_data_volume_as_the_standalone_file():
    """Gleicher Projektname und gleicher Datenspeicher: Der Wechsel zwischen beiden Dateien behaelt die Daten."""
    helper, standalone = _helper(), _load(STANDALONE)
    assert helper["name"] == standalone["name"] == "nodvard-deck"
    assert helper["volumes"]["nodvard_deck_data"] == standalone["volumes"]["nodvard_deck_data"] == {"name": "nodvard-deck-data"}


def test_every_volume_has_a_fixed_name():
    assert _helper()["volumes"] == {
        "nodvard_deck_data": {"name": "nodvard-deck-data"},
        "nodvard_deck_updater": {"name": "nodvard-deck-updater"},
        "nodvard_deck_updater_state": {"name": "nodvard-deck-updater-state"},
    }


def test_dashboard_never_sees_the_socket_or_the_helper_state():
    volumes = _helper()["services"]["nodvard-deck"]["volumes"]
    assert not any("docker.sock" in v or "nodvard_deck_updater_state" in v or v.endswith(":/state") for v in volumes)


# ---------------------------------------------------------------------------
# Compose: der Helfer
# ---------------------------------------------------------------------------


def test_helper_uses_the_published_image_with_tag_1_and_no_build():
    updater = _updater()
    release = _load(WORKFLOWS / "release.yml")
    assert updater["image"] == f"{release['env']['IMAGE_NAME']}-updater:1" == "ghcr.io/nodvard/deck-updater:1"
    assert "build" not in updater
    assert updater["restart"] == "unless-stopped"


def test_helper_has_every_protection_option():
    updater = _updater()
    assert updater["network_mode"] == "none", "kein Netz: das Herunterladen macht der Docker-Dienst"
    assert updater["read_only"] is True
    assert updater["cap_drop"] == ["ALL"]
    assert updater["security_opt"] == ["no-new-privileges:true"]
    assert updater["mem_limit"] == "64m"
    assert updater["pids_limit"] == 32
    assert updater["logging"] == _load(MAIN_COMPOSE)["services"]["nodvard-deck"]["logging"]


@pytest.mark.parametrize("key", ["ports", "expose", "networks", "privileged", "cap_add", "user", "userns_mode", "pid",
                                 "ipc", "devices", "tmpfs", "environment", "env_file", "command", "entrypoint",
                                 "healthcheck", "container_name", "hostname", "labels", "depends_on", "profiles"])
def test_helper_has_nothing_that_widens_or_changes_it(key):
    """Keine Ports, kein Netz, keine zusaetzlichen Rechte; Einstiegspunkt, Pruefung und Name kommen aus dem Image bzw.
    von Compose (der Helfer erkennt sich und sein Projekt an den Compose-Labels)."""
    assert key not in _updater()


def test_helper_volumes_socket_without_ro_channel_and_state():
    """`:ro` schuetzt an einem Socket nicht (Docker liest und schreibt darueber trotzdem) und wuerde falsche Sicherheit
    vortaeuschen. Der Pfad laesst sich fuer Rootless Docker ueber eine Variable setzen."""
    assert _updater()["volumes"] == [SOCKET_LINE, "nodvard_deck_updater:/channel", "nodvard_deck_updater_state:/state"]


def test_helper_service_name_variable_is_only_a_commented_example():
    text = HELPER_COMPOSE.read_text(encoding="utf-8")
    assert "#   NODVARD_DECK_UPDATER_SERVICE: nodvard-deck" in text


def _header(path: Path) -> str:
    """Der Kopf einer Compose-Datei: alles vor `name:`."""
    text = path.read_text(encoding="utf-8")
    return text[: text.index("\nname:")]


def test_header_warns_and_explains_the_setup_in_german():
    head = _header(HELPER_COMPOSE)
    assert all(line.startswith("#") or not line for line in head.splitlines())
    assert "-o compose.yml" in head and "compose.standalone-mit-helfer.yml" in head
    assert "docker compose up -d" in head and "Browser" in head
    assert "root" in head and "kein Netz" in head, "Warnung: Zugriff auf Docker ist so viel wie root"
    assert "EINEN Stack" in head, "Portainer/Synology: Helfer und Dashboard im selben Projekt"
    assert "docker compose pull updater && docker compose up -d updater" in head


def test_header_says_what_to_carry_over_before_replacing_the_old_file():
    """"Gleiche Daten" gilt nur fuer den benannten Datenspeicher. Wer in compose.standalone.yml einen Ordner eingetragen
    hat (./daten:/app/data, dort ausdruecklich angeboten), startete nach dem Ersetzen mit leeren Daten."""
    head = _header(HELPER_COMPOSE)
    assert "./daten:/app/data" in "\n".join(_service_block(STANDALONE, "nodvard-deck")), "Vorbedingung: dort angeboten"
    assert "cp compose.yml compose.yml.alt" in head
    assert head.index("cp compose.yml compose.yml.alt") < head.index("curl -fsSL -o compose.yml")
    assert "./daten:/app/data" in head and "leeren Daten" in head
    for other in ("Port", "Sicherungsordner", "DNS"):
        assert other in head, other


def _min_version() -> str:
    from updater_helpers import helper_module

    return helper_module("policy").MIN_VERSION


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _frontend_has_the_update_button() -> bool:
    """Die Oberflaeche ruft `POST /system/updates/apply` auf (Knopf "Jetzt aktualisieren"); Tests zaehlen nicht."""
    for path in (ROOT / "frontend" / "src").rglob("*.ts*"):
        if ".test." in path.name or path.suffix not in {".ts", ".tsx"}:
            continue
        if "updates/apply" in path.read_text(encoding="utf-8"):
            return True
    return False


def test_header_names_the_minimum_version_of_the_helper():
    """Unter `MIN_VERSION` lehnt der Helfer ab (`version_too_old`), und vorher gibt es sein Image noch nicht."""
    assert f"Erst ab Nodvard Deck {_min_version()}." in _header(HELPER_COMPOSE)


def test_standalone_points_to_the_helper_file_only_once_it_can_be_used():
    """compose.standalone.yml laden Nutzer von `main`. Ein Hinweis auf die Datei mit Helfer schickte sie sonst zu einem
    Knopf, den es noch nicht gibt, oder zu einem Helfer, der die Version ablehnt (oder dessen Image es noch nicht gibt:
    dann startet `docker compose up -d` gar nichts, auch das Dashboard nicht). Den Hinweis erst aufnehmen, wenn
    version.py mindestens MIN_VERSION des Helfers ist und die Oberflaeche den Knopf hat."""
    from nodvard_deck.version import __version__

    usable = _version_tuple(__version__) >= _version_tuple(_min_version()) and _frontend_has_the_update_button()
    if not usable:
        assert "compose.standalone-mit-helfer.yml" not in STANDALONE.read_text(encoding="utf-8"), (
            f"Hinweis auf den Helfer erst ab Version {_min_version()} und mit dem Knopf (jetzt {__version__})")
    assert list(_load(STANDALONE)["services"]) == ["nodvard-deck"]


def test_main_compose_file_only_warns_about_the_helper_file():
    """docker-compose.yml (Pi-Deploy, selbst gebautes Image) bekommt keinen Helfer, nur den Hinweis, warum nicht und
    dass ein Wechsel der Datei dort mit leeren Daten startete (anderer Datenspeicher)."""
    header = _header(MAIN_COMPOSE)
    assert "compose.standalone-mit-helfer.yml" in header and "deploy_pi.sh" in header
    assert "deploy_lattice_data" in header and "leeren Daten" in header
    assert list(_load(MAIN_COMPOSE)["services"]) == ["nodvard-deck"]


# ---------------------------------------------------------------------------
# Compose: `docker compose config`
# ---------------------------------------------------------------------------


def _config(tmp_path, env=None, args=()):
    target = tmp_path / "irgendein-ordner"
    target.mkdir(exist_ok=True)
    shutil.copy(HELPER_COMPOSE, target / "compose.yml")  # nur diese eine Datei, unter dem Namen aus dem Kopf
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "NODVARD_DECK_", "LATTICE_"))}
    clean.update(env or {})
    quiet = subprocess.run(["docker", "compose", *args, "config", "-q"], cwd=target, env=clean, capture_output=True,
                           text=True, timeout=60, check=False)
    if quiet.returncode != 0 and "is not a docker command" in quiet.stderr:
        pytest.skip("docker compose fehlt")
    assert quiet.returncode == 0, quiet.stderr
    result = subprocess.run(["docker", "compose", *args, "config", "--format", "json"], cwd=target, env=clean,
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker wird gebraucht")
def test_docker_compose_config_accepts_the_file_alone(tmp_path):
    config = _config(tmp_path)
    assert config["name"] == "nodvard-deck"
    updater = config["services"]["updater"]
    assert updater["network_mode"] == "none" and updater["read_only"] is True and updater["cap_drop"] == ["ALL"]
    assert not updater.get("ports")
    socket, channel, state = updater["volumes"]
    assert (socket["type"], socket["source"], socket["target"]) == ("bind", "/var/run/docker.sock", "/var/run/docker.sock")
    assert socket.get("read_only") is not True
    assert (channel["source"], channel["target"]) == ("nodvard_deck_updater", "/channel")
    assert (state["source"], state["target"]) == ("nodvard_deck_updater_state", "/state")
    dashboard_targets = [v["target"] for v in config["services"]["nodvard-deck"]["volumes"]]
    assert dashboard_targets == ["/app/data", "/app/updater"]


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker wird gebraucht")
@pytest.mark.parametrize(("env", "args"), [({"COMPOSE_PROJECT_NAME": "foo"}, ()), ({}, ("-p", "foo"))], ids=["env", "-p"])
def test_overriding_the_project_name_keeps_all_volume_names(tmp_path, env, args):
    config = _config(tmp_path, env, args)
    assert config["name"] == "foo", "der Test ueberstimmt den Projektnamen wirklich"
    assert {key: value["name"] for key, value in config["volumes"].items()} == {
        "nodvard_deck_data": "nodvard-deck-data",
        "nodvard_deck_updater": "nodvard-deck-updater",
        "nodvard_deck_updater_state": "nodvard-deck-updater-state",
    }


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker wird gebraucht")
def test_rootless_socket_path_comes_from_the_variable(tmp_path):
    config = _config(tmp_path, {"NODVARD_DECK_DOCKER_SOCKET": "/run/user/1000/docker.sock"})
    socket = config["services"]["updater"]["volumes"][0]
    assert (socket["source"], socket["target"]) == ("/run/user/1000/docker.sock", "/var/run/docker.sock")


# ---------------------------------------------------------------------------
# Dockerfile des Helfers
# ---------------------------------------------------------------------------


def _instructions(path: Path) -> list[tuple[str, str]]:
    """(BEFEHL, Rest) je Anweisung, Fortsetzungszeilen zusammengezogen, Kommentare weg."""
    out: list[tuple[str, str]] = []
    current = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not current and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            current += line[:-1] + " "
            continue
        current += line
        keyword, _, rest = current.partition(" ")
        out.append((keyword.upper(), rest.strip()))
        current = ""
    return out


def _dockerfile(keyword: str) -> list[str]:
    return [rest for kw, rest in _instructions(HELPER_DOCKERFILE) if kw == keyword]


def test_helper_base_image_is_pinned_by_digest():
    """Der Helfer laeuft mit Zugriff auf Docker: ein verschobenes Tag darf keinen fremden Code ins Image bringen."""
    froms = _dockerfile("FROM")
    assert len(froms) == 1, "eine Stufe, kein zweites Basis-Image"
    assert re.fullmatch(r"python:3\.12-slim@sha256:[0-9a-f]{64}", froms[0]), froms[0]


def test_helper_uses_the_python_of_the_dashboard_image_and_its_site_packages():
    dashboard_from = next(rest for kw, rest in _instructions(DASHBOARD_DOCKERFILE) if kw == "FROM" and "AS runtime" in rest)
    tag = dashboard_from.split()[0]
    assert _dockerfile("FROM")[0].split("@")[0] == tag
    version = re.fullmatch(r"python:(\d+\.\d+)-slim", tag)[1]
    for rest in _dockerfile("COPY"):
        if "nodvard_deck_updater" in rest:
            assert rest.split()[-1] == f"/usr/local/lib/python{version}/site-packages/nodvard_deck_updater"


def test_helper_copies_only_the_package_and_the_license():
    assert _dockerfile("ADD") == []
    copies = [rest.split() for rest in _dockerfile("COPY")]
    assert all(not part.startswith("--from") for parts in copies for part in parts)
    assert sorted(parts[0] for parts in copies) == ["LICENSE", "deploy/updater/nodvard_deck_updater"]
    assert all(len(parts) == 2 for parts in copies), copies


def test_helper_entrypoint_runs_isolated_and_there_is_no_volume_healthcheck_or_user():
    assert [json.loads(rest) for rest in _dockerfile("ENTRYPOINT")] == [ENTRYPOINT]
    assert _dockerfile("CMD") == []
    # Kein VOLUME: /channel und /state bekaemen sonst beim ersten Einhaengen Besitzer und Rechte aus dem Image.
    # Kein HEALTHCHECK: ein ausgefallener Helfer faellt im Dashboard am alten Lebenszeichen auf.
    # Kein USER: der Helfer erwartet root als Besitzer von /channel und /state (ohne Capabilities, siehe Compose).
    for keyword in ("VOLUME", "HEALTHCHECK", "USER", "EXPOSE", "WORKDIR", "SHELL", "ONBUILD"):
        assert _dockerfile(keyword) == [], keyword
    runs = " ".join(_dockerfile("RUN"))
    for forbidden in ("apt-get", "apk ", "pip ", "curl", "wget", "/channel", "/state"):
        assert forbidden not in runs, forbidden


def test_helper_image_labels():
    labels = " ".join(_dockerfile("LABEL"))
    assert 'org.opencontainers.image.version="${VERSION}"' in labels
    assert 'org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0"' in labels
    assert "org.opencontainers.image.source=" in labels and "org.opencontainers.image.title=" in labels
    assert _dockerfile("ARG") == ['VERSION=""']


def test_helper_license_matches_the_dashboard_image():
    """Gleiche Lizenzangabe wie das Dashboard-Image (release.yml setzt sie dort als Label)."""
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "org.opencontainers.image.licenses=PolyForm-Noncommercial-1.0.0" in release
    assert "PolyForm Noncommercial License 1.0.0" in (ROOT / "LICENSE").read_text(encoding="utf-8")


def test_the_dashboard_image_never_knows_the_channel_or_the_helper():
    """Bringt das Dashboard-Image /app/updater mit, gehoerte die Wurzel des Kanals nach dem ersten Einhaengen uid 1000
    (Copy-up), und der Helfer verweigerte den Kanal."""
    for path in (DASHBOARD_DOCKERFILE, DEPLOY / "entrypoint.sh"):
        assert "/app/updater" not in path.read_text(encoding="utf-8"), path.name
    # Nur die letzte Stufe wird zum Image. Die Bau-Stufe des Frontends darf die Test-Vektoren des Helfers lesen
    # (tsc prueft updaterTexts.test.ts mit), sie holt sich die letzte Stufe aber nie.
    instructions = _instructions(DASHBOARD_DOCKERFILE)
    last_stage = max(i for i, (kw, _rest) in enumerate(instructions) if kw == "FROM")
    final = instructions[last_stage:]
    assert not any("updater" in rest for kw, rest in final if kw in {"COPY", "ADD", "VOLUME", "RUN"})
    assert all(rest.split()[-2].startswith(("/src/extensions", "/src/frontend/dist"))
               for kw, rest in final if kw == "COPY" and rest.startswith("--from="))


def test_dockerignore_keeps_the_package_and_drops_bytecode_and_secrets():
    from test_dockerignore import ignored

    for path in ("deploy/updater/nodvard_deck_updater/__init__.py", "deploy/updater/nodvard_deck_updater/flow.py", "LICENSE"):
        assert not ignored(path), path
    # Alter Bytecode vom Entwickler-Rechner kommt nicht mit (das Image erzeugt ihn selbst), Geheimnisse auch nicht.
    for path in ("deploy/updater/nodvard_deck_updater/__pycache__/flow.cpython-312.pyc", "deploy/updater/.env",
                 "deploy/updater/.venv/bin/python", "deploy/updater/nodvard_deck_updater/data/master.key"):
        assert ignored(path), path
    assert {p.name for p in PACKAGE_DIR.glob("*.py")} >= {"__init__.py", "__main__.py"}


# ---------------------------------------------------------------------------
# CI: Image bauen, Selbsttest, Groesse
# ---------------------------------------------------------------------------


def _ci_updater_job() -> dict:
    return _load(WORKFLOWS / "ci.yml")["jobs"]["updater"]


def _ci_updater_steps() -> list[dict]:
    return _ci_updater_job()["steps"]


def _ci_image_tag() -> str:
    runs = [s.get("run", "").strip() for s in _ci_updater_steps()]
    tags = [m[1] for r in runs if (m := re.fullmatch(r"docker build -f deploy/updater/Dockerfile -t (\S+) \.", r))]
    assert len(tags) == 1, runs
    return tags[0]


def _limit_flags() -> tuple[str, str]:
    """Speicher- und Prozessgrenze genau wie beim Dienst `updater` in der Compose-Datei."""
    updater = _updater()
    return f"--memory {updater['mem_limit']}", f"--pids-limit {updater['pids_limit']}"


def test_ci_builds_the_helper_image_from_the_repository_root():
    assert _ci_image_tag()


def test_ci_runs_the_selftest_like_in_production():
    runs = [s.get("run", "").strip() for s in _ci_updater_steps()]
    selftest = [r for r in runs if r.startswith("docker run") and r.endswith("--selftest")]
    assert len(selftest) == 1, runs
    for flag in (*SELFTEST_FLAGS, *_limit_flags()):
        assert flag in selftest[0], flag
    assert selftest[0].endswith(f" {_ci_image_tag()} --selftest")


def test_ci_helper_job_cannot_be_skipped_or_ignored():
    """Mit `continue-on-error`, einem `if` oder `|| true` bliebe der Job gruen, obwohl Selbsttest oder Groessengrenze
    scheitern."""
    job = _ci_updater_job()
    assert set(job) == {"runs-on", "steps"}, sorted(job)
    for step in job["steps"]:
        assert set(step) <= {"name", "uses", "run"}, step
        run = step.get("run", "")
        assert "||" not in run and "set +e" not in run, run


def _size_step() -> str:
    found = [s["run"] for s in _ci_updater_steps() if s.get("name") == "Groesse unter 160 MB"]
    assert len(found) == 1
    return found[0]


def _run_size_step(tmp_path: Path, output: str, exit_code: int = 0):
    """Der Schritt mit einem nachgebauten `docker`, in der Shell, die GitHub ohne `shell:` nimmt (`bash -e`, ohne
    pipefail). Der nachgebaute Befehl merkt sich seine Argumente."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("braucht bash")
    (tmp_path / "out").write_text(output, encoding="utf-8")
    fake = tmp_path / "docker"
    fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{tmp_path / "args"}"\ncat "{tmp_path / "out"}"\nexit {exit_code}\n',
                    encoding="utf-8")
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}"}
    return subprocess.run([bash, "-e", "-c", _size_step()], env=env, capture_output=True, text=True, check=False)


@pytest.mark.parametrize(("output", "exit_code", "expected"), [
    ("159999999\t/\n", 0, 0),
    ("160000000\t/\n", 0, 1),
    ("987654321\t/\n", 0, 1),
    ("", 0, 1),
    ("du: kaputt\n", 0, 1),
    ("-1\t/\n", 0, 1),
    ("1000\t/\n", 1, 1),  # du meldet einen Fehler (z. B. Ordner nicht lesbar): nicht mit einer Teilsumme weiter
], ids=["knapp-darunter", "genau-160", "viel-groesser", "leer", "kein-wert", "negativ", "du-fehler"])
def test_ci_keeps_the_helper_image_below_160_mb(tmp_path, output, exit_code, expected):
    done = _run_size_step(tmp_path, output, exit_code)
    assert (done.returncode != 0) == bool(expected), (done.stdout, done.stderr)


def test_ci_measures_the_files_in_the_image_not_what_the_engine_reports(tmp_path):
    """`docker image inspect` meldet beim containerd-Speicher die gepackte Groesse (rund ein Drittel): Die Grenze waere
    dort fast wirkungslos. Gemessen wird darum mit du im Container, ohne Netz, nur lesbar, nur mit dem Recht, alles zu
    lesen."""
    assert "image inspect" not in _size_step() and "{{.Size}}" not in _size_step()
    done = _run_size_step(tmp_path, "117744659\t/\n")
    assert done.returncode == 0, done.stderr
    args = (tmp_path / "args").read_text(encoding="utf-8").splitlines()
    assert args == ["run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--cap-add", "DAC_READ_SEARCH",
                    "--entrypoint", "du", _ci_image_tag(), "-sxb", "/"]


def test_ci_keeps_the_existing_helper_test_runs_in_the_backend_job():
    runs = [s.get("run", "") for s in _load(WORKFLOWS / "ci.yml")["jobs"]["backend"]["steps"]]
    assert "python -m pytest deploy/updater/tests" in runs
    assert any("needs_root deploy/updater/tests" in r for r in runs)
