"""`.github/workflows/release.yml`: Image bei jedem Tag `v*` bauen und nach ghcr.io schieben.

Kein Deploy, keine eigenen Geheimnisse: nur der automatisch vergebene `GITHUB_TOKEN`. Der Image-Name
steht ganz oben als Variable, weil die Organisation "nodvard" erst angelegt werden muss. Der zweite Job
`updater-image` baut danach das Image des Update-Helfers (deploy/updater) unter demselben Namen mit "-updater",
der dritte `dock-image` Nodvard Dock (nur das Modul docker). Die Tests lesen die YAML-Struktur und führen einzelne
Schritte lokal aus (kein Lauf bei GitHub).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"


def _workflow() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(RELEASE.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _workflow()["jobs"]["image"]["steps"]


def _step(uses_prefix: str) -> dict:
    found = [s for s in _steps() if str(s.get("uses", "")).split("@")[0] == uses_prefix]
    assert len(found) == 1, f"genau ein Schritt mit {uses_prefix} erwartet"
    return found[0]


def test_runs_only_on_version_tags():
    # PyYAML liest den Schluessel `on` als True (YAML 1.1) -- beides zulassen.
    triggers = _workflow().get("on", _workflow().get(True))
    assert set(triggers) == {"push"}, "nur Tags, kein Pull-Request- oder Branch-Trigger"
    # Nur echte Versionsnummern (v1.2.3, v1.2.3-rc1): ein Tag wie `vtest` darf `latest` nicht ueberschreiben.
    assert triggers["push"] == {"tags": ["v[0-9]+.[0-9]+.[0-9]+*"]}


def test_image_name_is_a_variable_at_the_top():
    workflow = _workflow()
    assert workflow["env"]["IMAGE_NAME"] == "ghcr.io/nodvard/deck"
    assert _step("docker/metadata-action")["with"]["images"] == "${{ env.IMAGE_NAME }}"
    # Nirgends sonst fest eingetragen, sonst muesste man beim Umzug der Organisation suchen.
    assert RELEASE.read_text(encoding="utf-8").count("ghcr.io/nodvard/deck") == 1


def test_builds_both_architectures_with_qemu_and_buildx():
    _step("docker/setup-qemu-action")
    _step("docker/setup-buildx-action")
    build = _step("docker/build-push-action")["with"]
    assert build["platforms"] == "linux/amd64,linux/arm64"
    assert build["file"] == "deploy/Dockerfile"
    assert build["context"] == "."
    assert build["push"] is True
    assert build["tags"] == "${{ steps.meta.outputs.tags }}"
    assert build["labels"] == "${{ steps.meta.outputs.labels }}"


def test_tags_are_version_major_minor_and_latest():
    meta = _step("docker/metadata-action")["with"]
    tags = meta["tags"]
    assert "type=semver,pattern={{version}}" in tags
    assert "type=semver,pattern={{major}}.{{minor}}" in tags
    assert "type=raw,value=latest" in tags
    # `latest` nie automatisch, nur für die höchste fertige Version.
    assert "latest=false" in meta["flavor"]
    assert "type=raw,value=latest,enable=${{ steps.newest.outputs.newest == 'true' }}" in tags


def test_oci_labels_source_license_version():
    labels = _step("docker/metadata-action")["with"]["labels"]
    assert "org.opencontainers.image.source=" in labels
    assert "org.opencontainers.image.licenses=PolyForm-Noncommercial-1.0.0" in labels
    assert "org.opencontainers.image.version=" in labels


def test_login_uses_only_the_github_token_and_least_privilege():
    workflow = _workflow()
    job = workflow["jobs"]["image"]
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert workflow.get("permissions", {}) in ({}, {"contents": "read"})
    login = _step("docker/login-action")["with"]
    assert login["registry"] == "ghcr.io"
    assert login["password"] == "${{ secrets.GITHUB_TOKEN }}"
    # Keine anderen Geheimnisse, kein Deploy. Jeder Job meldet sich einzeln an, immer nur mit dem GITHUB_TOKEN.
    import re

    text = RELEASE.read_text(encoding="utf-8")
    # Jede Erwaehnung von `secrets` ausser `secrets.GITHUB_TOKEN`, auch `secrets['X']` und `toJSON(secrets)`.
    assert re.findall(r"\bsecrets\b(?!\.GITHUB_TOKEN\b)", text) == []
    assert text.count("secrets.GITHUB_TOKEN") == len(workflow["jobs"])
    for name, other in workflow["jobs"].items():
        if name == "helper-release":
            # Legt das GitHub-Release an: Schreibrecht aufs Repository, keins auf die Registry.
            assert other["permissions"] == {"contents": "write"}
            continue
        assert other["permissions"] == {"contents": "read", "packages": "write"}, name
    for forbidden in ("ssh", "scp", "deploy_pi", "docker compose", "kubectl"):
        assert forbidden not in text.lower().replace("docker/", ""), forbidden


def test_existing_workflows_are_untouched_by_this_file():
    assert sorted(p.name for p in WORKFLOWS.glob("*.yml")) == ["ci.yml", "release.yml"]


def test_every_action_is_pinned_to_a_commit_sha_with_the_version_as_comment():
    import re

    lines = [line for line in RELEASE.read_text(encoding="utf-8").splitlines() if re.search(r"\buses:", line)]
    assert len(lines) >= 6
    for line in lines:
        assert re.search(r"uses: [\w./-]+@[0-9a-f]{40}  # v\d+\.\d+\.\d+$", line), f"nicht auf eine Commit-SHA festgelegt: {line.strip()}"


def test_build_args_name_version_and_official_image():
    build = _step("docker/build-push-action")["with"]
    args = dict(line.split("=", 1) for line in build["build-args"].strip().splitlines())
    assert args == {"VERSION": "${{ env.VERSION }}", "IMAGE": "${{ env.IMAGE_NAME }}", "APT_REFRESH": "${{ github.run_id }}"}


def test_every_release_fetches_the_debian_packages_fresh():
    text = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    runtime = text[text.index("AS runtime"):]
    apt_run = runtime.rindex("\nRUN ", 0, runtime.index("apt-get update"))
    # Direkt davor: Ein neuer Wert baut sonst auch die Schritte dazwischen neu.
    assert runtime[:apt_run].endswith('\nARG APT_REFRESH=""')


def test_dockerfile_writes_the_build_args_into_a_file_not_the_environment():
    """Herkunft und Version stehen in einer Datei im Image (`nodvard_deck.image_info`), nicht in einer ENV: Die
    ENV eines Images wandert in die Einstellungen jedes Containers und kaeme beim Neuanlegen mit neuem Image mit."""
    import re

    from nodvard_deck import image_info

    text = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    runtime = text[text.index("AS runtime"):]
    assert re.search(r'^ARG VERSION=""$', runtime, re.MULTILINE)
    assert re.search(r'^ARG IMAGE=""$', runtime, re.MULTILINE)
    run = f"RUN python -m nodvard_deck.image_info {image_info.PATH.as_posix()}"
    assert re.search(rf"^{re.escape(run)}$", runtime, re.MULTILINE)
    assert runtime.index(run) > runtime.index("ARG IMAGE") > runtime.index("ARG VERSION")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "NODVARD_DECK_IMAGE" not in code and "NODVARD_DECK_BUILD" not in code and "LATTICE_BUILD" not in code
    # Erst nach den Paketen, sonst baut jede neue Version alles neu.
    assert runtime.index("ARG VERSION") > runtime.index("pip install --no-cache-dir -c constraints.txt ./backend")


def test_official_image_constant_matches_the_workflow():
    from nodvard_deck.core import updates

    assert _workflow()["env"]["IMAGE_NAME"] == updates.OFFICIAL_IMAGE


def _named(name: str) -> tuple[int, dict]:
    found = [(i, s) for i, s in enumerate(_steps()) if s.get("name") == name]
    assert len(found) == 1, f"genau ein Schritt {name!r} erwartet"
    return found[0]


def test_the_grant_guide_names_the_build_step_that_prints_the_digest():
    _, build = _named("Bauen und veroeffentlichen")
    assert build["uses"].split("@")[0] == "docker/build-push-action"
    guide_path = ROOT / "docs" / "VEROEFFENTLICHEN.md"
    if not guide_path.exists():
        pytest.skip("Die Anleitung fehlt im öffentlichen Export (.gitattributes, export-ignore).")
    guide = " ".join(guide_path.read_text(encoding="utf-8").split())
    assert "Job `image`, Schritt „Bauen und veroeffentlichen“" in guide


def test_tag_must_match_version_py_before_anything_is_built():
    """Sonst entsteht ein Image `:0.6.0`, dessen Code `0.5.0` sagt -- es meldete dauerhaft ein Update auf sich selbst."""
    tag_index, _ = _named("Version aus dem Tag")
    guard_index, guard = _named("Tag passt zu version.py")
    build_index = next(i for i, s in enumerate(_steps()) if str(s.get("uses", "")).startswith("docker/build-push-action@"))
    assert tag_index < guard_index < build_index
    run = guard["run"]
    assert "${VERSION%%-*}" in run and "backend/src/nodvard_deck/version.py" in run and "exit 1" in run
    assert "base >= code" in run, "eine Vorabversion darf die kommende Version nennen, nie eine aeltere"
    assert "${{" not in run, "VERSION nur ueber die Umgebung, nie in den Befehl eingesetzt"


@pytest.mark.parametrize("suffix", ["", "-rc1", "-rc.2", "-x-y"])
def test_tag_guard_accepts_the_code_version_and_its_prereleases(suffix):
    from nodvard_deck.version import __version__

    done = _run_guard(__version__ + suffix)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("tag", ["-rc1", "-rc.2", "-beta"])
def test_tag_guard_accepts_a_prerelease_of_an_upcoming_version(tag):
    """Der rc kommt vor `release.py`: version.py ist noch die alte, das Tag nennt schon die kommende."""
    from nodvard_deck.version import __version__

    major, minor, patch = (int(p) for p in __version__.split("."))
    for upcoming in (f"{major}.{minor}.{patch + 1}", f"{major}.{minor + 1}.0", f"{major + 1}.0.0"):
        done = _run_guard(upcoming + tag)
        assert done.returncode == 0, (upcoming, done.stderr)


@pytest.mark.parametrize("tag", ["9.9.9", "0.0.0", "", "9.9.9.9", "x"])
def test_tag_guard_stops_on_a_mismatch_of_a_final_version(tag):
    """Eine fertige Version muss genau version.py sein, auch eine groessere (release.py laeuft vor dem Tag)."""
    from nodvard_deck.version import __version__

    assert tag != __version__
    done = _run_guard(tag)
    assert done.returncode == 1
    assert "passt nicht" in done.stderr


@pytest.mark.parametrize("tag", ["0.0.0-rc1", "0.0.1-beta", "9.9.9.9-rc1", "x-rc1", "1-rc1", "-rc1", "9.9.9-rc1+b1", "9.9.9-rc_1", "9.9.9-", "09.9.9-rc1"])
def test_tag_guard_stops_on_a_prerelease_of_an_older_or_malformed_version(tag):
    done = _run_guard(tag)
    assert done.returncode == 1
    assert "passt nicht" in done.stderr


def _run_guard(version: str):
    import os
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None or shutil.which("python3") is None:
        pytest.skip("braucht bash und python3 (wie der Runner von GitHub)")
    _, guard = _named("Tag passt zu version.py")
    env = {**os.environ, "VERSION": version}
    return subprocess.run([bash, "-eo", "pipefail", "-c", guard["run"]], cwd=ROOT, env=env, capture_output=True, text=True, check=False)


def test_newest_version_is_decided_after_the_tag_guard_and_before_the_tags():
    guard_index, _ = _named("Tag passt zu version.py")
    newest_index, newest = _named("Höchste fertige Version")
    meta_index = next(i for i, s in enumerate(_steps()) if str(s.get("uses", "")).startswith("docker/metadata-action@"))
    assert guard_index < newest_index < meta_index
    assert newest["id"] == "newest"
    assert "${{" not in newest["run"], "VERSION nur über die Umgebung, nie in den Befehl eingesetzt"
    assert "if" not in newest and "continue-on-error" not in newest, "ohne Ergebnis darf der Lauf nicht weitergehen"
    assert _workflow()["jobs"]["image"]["outputs"] == {"newest": "${{ steps.newest.outputs.newest }}"}


def _run_newest(tmp_path: Path, version: str, tags: list[str], *, git_exit: int = 0):
    """Führt den Schritt wie der Runner aus, mit einem `git`, das die Tags wie `git ls-remote` ausgibt."""
    import os
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None or shutil.which("python3") is None:
        pytest.skip("braucht bash und python3 (wie der Runner von GitHub)")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    listing = tmp_path / "tags"
    listing.write_text("".join(f"{'a' * 40}\trefs/tags/v{tag}\n" for tag in tags), encoding="utf-8")
    (bin_dir / "git").write_text(f'#!/bin/sh\ncat "{listing}"\nexit {git_exit}\n', encoding="utf-8")
    (bin_dir / "git").chmod(0o755)
    output = tmp_path / "output"
    output.write_text("", encoding="utf-8")
    _, step = _named("Höchste fertige Version")
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", "VERSION": version,
           "GITHUB_OUTPUT": str(output)}
    # Ohne `shell:` startet der Runner `bash -e {0}`, pipefail nur bei `shell: bash`.
    flags = ["-eo", "pipefail"] if step.get("shell") == "bash" else ["-e"]
    done = subprocess.run([bash, *flags, "-c", step["run"]], cwd=ROOT, env=env, capture_output=True, text=True,
                          check=False)
    return done, output.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("version", "tags", "newest"),
    [
        ("0.13.0", ["0.12.0", "0.12.1", "0.13.0"], "true"),
        ("0.12.2", ["0.12.0", "0.12.1", "0.13.0", "0.12.2"], "false"),
        ("0.12.2", ["0.12.1", "0.13.0-rc1", "0.12.2"], "true"),
        ("0.13.0-rc1", ["0.12.1", "0.13.0-rc1"], "false"),
    ],
    ids=["hoechste", "patch-einer-aelteren-reihe", "nur-ein-rc-darueber", "vorabversion"],
)
def test_newest_step_writes_the_output(tmp_path, version, tags, newest):
    done, output = _run_newest(tmp_path, version, tags)
    assert done.returncode == 0, done.stderr
    assert output == f"newest={newest}\n"


@pytest.mark.parametrize("tags", [[], ["0.12.1", "0.13.0"]], ids=["ohne-ausgabe", "liste-dann-fehler"])
def test_newest_step_stops_when_git_fails(tmp_path, tags):
    done, output = _run_newest(tmp_path, "0.13.0", tags, git_exit=128)
    assert done.returncode != 0
    assert output == ""


# ---------------------------------------------------------------------------
# Job `updater-image`: das Image des Update-Helfers (deploy/updater)
# ---------------------------------------------------------------------------


def _updater_steps() -> list[dict]:
    return _workflow()["jobs"]["updater-image"]["steps"]


def _updater_step(uses_prefix: str) -> tuple[int, dict]:
    found = [(i, s) for i, s in enumerate(_updater_steps()) if str(s.get("uses", "")).split("@")[0] == uses_prefix]
    assert len(found) == 1, f"genau ein Schritt mit {uses_prefix} erwartet"
    return found[0]


def _updater_named(name: str) -> tuple[int, dict]:
    found = [(i, s) for i, s in enumerate(_updater_steps()) if s.get("name") == name]
    assert len(found) == 1, f"genau ein Schritt {name!r} erwartet"
    return found[0]


def test_jobs_are_the_dashboard_the_helper_dock_and_the_release():
    assert list(_workflow()["jobs"]) == ["image", "updater-image", "dock-image", "helper-release"]


def test_helper_image_waits_for_the_dashboard_image():
    """Erst wenn das Dashboard-Image gebaut ist (der Schritt "Tag passt zu version.py" also durch ist), entsteht der
    Helfer zum selben Tag. Ein abgelehntes Tag bringt so auch keinen Helfer."""
    assert _workflow()["jobs"]["updater-image"]["needs"] == "image"


def test_helper_image_is_built_from_deploy_updater_for_both_architectures():
    _updater_step("docker/setup-qemu-action")
    _updater_step("docker/setup-buildx-action")
    _, build = _updater_step("docker/build-push-action")
    args = build["with"]
    assert build["id"] == "build"
    assert args["file"] == "deploy/updater/Dockerfile"
    assert args["context"] == "."
    assert args["platforms"] == "linux/amd64,linux/arm64"
    assert args["push"] is True
    assert args["tags"] == "${{ steps.meta.outputs.tags }}"
    assert args["labels"] == "${{ steps.meta.outputs.labels }}"
    assert dict(line.split("=", 1) for line in args["build-args"].strip().splitlines()) == {"VERSION": "${{ env.VERSION }}"}
    # Kein gemeinsamer Build-Cache mit dem Dashboard-Image (und keiner, der in ein Image mit Docker-Zugriff wandert).
    assert "cache-from" not in args and "cache-to" not in args


def test_helper_image_name_follows_the_dashboard_image_name():
    """Nur `IMAGE_NAME` + "-updater": zieht die Organisation um, aendert sich beides an einer Stelle."""
    _, meta = _updater_step("docker/metadata-action")
    assert meta["with"]["images"] == "${{ env.IMAGE_NAME }}-updater"
    assert "deck-updater" not in RELEASE.read_text(encoding="utf-8")


def test_helper_image_tags_and_labels():
    _, meta = _updater_step("docker/metadata-action")
    args = meta["with"]
    assert "latest=false" in args["flavor"]
    assert [line.strip() for line in args["tags"].strip().splitlines()] == ["type=semver,pattern={{version}}"]
    labels = args["labels"]
    for label in ("org.opencontainers.image.title=", "org.opencontainers.image.source=", "org.opencontainers.image.version=${{ env.VERSION }}",
                  "org.opencontainers.image.licenses=PolyForm-Noncommercial-1.0.0"):
        assert label in labels, label


def test_helper_version_comes_from_the_tag_like_for_the_dashboard():
    _, step = _updater_named("Version aus dem Tag")
    _, dashboard = _named("Version aus dem Tag")
    assert step == dashboard


def test_helper_login_only_with_the_github_token():
    _, login = _updater_step("docker/login-action")
    assert login["with"] == {"registry": "ghcr.io", "username": "${{ github.actor }}", "password": "${{ secrets.GITHUB_TOKEN }}"}


def test_helper_actions_are_the_same_pinned_commits_as_for_the_dashboard():
    dashboard = {str(s["uses"]).split("@")[0]: s["uses"] for s in _steps() if "uses" in s}
    for step in _updater_steps():
        if "uses" in step:
            assert step["uses"] == dashboard[str(step["uses"]).split("@")[0]], step["uses"]


def test_helper_selftest_runs_on_the_published_digest_for_both_platforms():
    build_index, _ = _updater_step("docker/build-push-action")
    test_index, step = _updater_named("Selbsttest am Digest (amd64 und arm64)")
    assert test_index > build_index
    assert step["env"] == {"IMAGE": "${{ env.IMAGE_NAME }}-updater", "DIGEST": "${{ steps.build.outputs.digest }}"}
    run = step["run"]
    assert "${{" not in run, "Image und Digest nur ueber die Umgebung, nie in den Befehl eingesetzt"
    assert "linux/amd64 linux/arm64" in run and '--platform "$platform"' in run
    pids = _helper_compose()["services"]["updater"]["pids_limit"]
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", f"--pids-limit {pids}"):
        assert flag in run, flag
    assert '"$IMAGE@$DIGEST" --selftest' in run


def _helper_compose() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((ROOT / "deploy" / "compose.standalone-mit-helfer.yml").read_text(encoding="utf-8"))


def test_helper_selftest_cannot_be_skipped_or_ignored():
    """Sonst wanderte `:1` (das nimmt die Compose-Datei) auch nach einem gescheiterten oder uebersprungenen Selbsttest:
    kein `if`, kein `continue-on-error`, kein `|| true` am Selbsttest, kein `continue-on-error` an irgendeinem Schritt,
    und auf Job-Ebene nur die bekannten Schluessel."""
    job = _workflow()["jobs"]["updater-image"]
    assert set(job) == {"needs", "runs-on", "permissions", "outputs", "steps"}, sorted(job)
    _, selftest = _updater_named("Selbsttest am Digest (amd64 und arm64)")
    assert set(selftest) == {"name", "env", "run"}, sorted(selftest)
    assert "||" not in selftest["run"] and "set +e" not in selftest["run"]
    for step in _updater_steps():
        assert "continue-on-error" not in step, step
        if "if" in step:
            assert step.get("name") == "Tag 1 setzen", step


def test_helper_tag_1_moves_only_after_the_selftest_and_only_for_the_newest_version():
    """`:1` nimmt die Compose-Datei mit Helfer: erst nach dem Selbsttest und nur für die höchste fertige Version."""
    test_index, _ = _updater_named("Selbsttest am Digest (amd64 und arm64)")
    tag_index, step = _updater_named("Tag 1 setzen")
    assert tag_index == test_index + 1 == len(_updater_steps()) - 1
    assert step["if"] == "${{ needs.image.outputs.newest == 'true' }}"
    assert step["env"] == {"IMAGE": "${{ env.IMAGE_NAME }}-updater", "DIGEST": "${{ steps.build.outputs.digest }}"}
    assert step["run"].strip() == 'docker buildx imagetools create --tag "$IMAGE:1" "$IMAGE@$DIGEST"'


# ---------------------------------------------------------------------------
# Job `updater-image`: das Tag passt zur Version des Helfers
# ---------------------------------------------------------------------------

HELPER_INIT = Path("deploy") / "updater" / "nodvard_deck_updater" / "__init__.py"


def _helper_version() -> str:
    import ast

    tree = ast.parse((ROOT / HELPER_INIT).read_text(encoding="utf-8"))
    found = [n.value.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
             and [getattr(t, "id", None) for t in n.targets] == ["__version__"]]
    assert len(found) == 1
    return found[0]


def _run_helper_guard(version: str, cwd: Path = ROOT):
    import os
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None or shutil.which("python3") is None:
        pytest.skip("braucht bash und python3 (wie der Runner von GitHub)")
    _, guard = _updater_named("Tag passt zur Version des Helfers")
    env = {**os.environ, "VERSION": version}
    return subprocess.run([bash, "-eo", "pipefail", "-c", guard["run"]], cwd=cwd, env=env, capture_output=True, text=True,
                          check=False)


def test_helper_tag_must_match_the_helper_version_before_anything_is_built():
    """Sonst entstuende ein Helfer-Image `:0.7.0`, dessen Status eine andere Version meldet (`helper_version`)."""
    tag_index, _ = _updater_named("Version aus dem Tag")
    guard_index, guard = _updater_named("Tag passt zur Version des Helfers")
    build_index, _ = _updater_step("docker/build-push-action")
    assert tag_index < guard_index < build_index
    assert set(guard) == {"name", "run"}, "kein if, kein continue-on-error"
    run = guard["run"]
    assert "${VERSION%%-*}" in run and HELPER_INIT.as_posix() in run and "exit 1" in run
    assert "base >= code" in run, "eine Vorabversion darf die kommende Version nennen, nie eine aeltere"
    assert "${{" not in run, "VERSION nur ueber die Umgebung, nie in den Befehl eingesetzt"


@pytest.mark.parametrize("suffix", ["", "-rc1", "-rc.2", "-x-y"])
def test_helper_guard_accepts_the_helper_version_and_its_prereleases(suffix):
    done = _run_helper_guard(_helper_version() + suffix)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("tag", ["-rc1", "-beta"])
def test_helper_guard_accepts_a_prerelease_of_an_upcoming_version(tag):
    major, minor, patch = (int(p) for p in _helper_version().split("."))
    for upcoming in (f"{major}.{minor}.{patch + 1}", f"{major}.{minor + 1}.0", f"{major + 1}.0.0"):
        done = _run_helper_guard(upcoming + tag)
        assert done.returncode == 0, (upcoming, done.stderr)


@pytest.mark.parametrize("tag", ["9.9.9", "0.0.0", "", "x", "0.0.0-rc1", "9.9.9-rc1+b1", "09.9.9-rc1"])
def test_helper_guard_stops_on_a_mismatch(tag):
    done = _run_helper_guard(tag)
    assert done.returncode == 1
    assert "passt nicht zur Version des Update-Helfers" in done.stderr


def test_helper_guard_stops_when_the_helper_was_not_raised_with_the_dashboard(tmp_path):
    """Der eigentliche Fall: version.py ist schon angehoben, der Helfer nicht (von Hand geaendert, Merge verloren). Das
    Dashboard-Image ist dann durch, der Helfer zum selben Tag darf trotzdem nicht entstehen."""
    from nodvard_deck.version import __version__

    major, minor, patch = (int(p) for p in __version__.split("."))
    older = f"{major}.{minor}.{patch - 1}" if patch else f"{major}.{max(minor - 1, 0)}.9"
    init = tmp_path / HELPER_INIT
    init.parent.mkdir(parents=True)
    init.write_text(f'"""Helfer."""\n\n__version__ = "{older}"\n"""Version."""\n', encoding="utf-8")
    done = _run_helper_guard(__version__, cwd=tmp_path)
    assert done.returncode == 1 and older in done.stderr
    init.write_text('"""Ohne Version."""\n', encoding="utf-8")
    done = _run_helper_guard(__version__, cwd=tmp_path)
    assert done.returncode == 1


def test_helper_version_is_the_dashboard_version():
    """`scripts/release.py` hebt beide gemeinsam an; die Wache prueft also dasselbe wie die im Job `image`."""
    from nodvard_deck.version import __version__

    assert _helper_version() == __version__


# ---------------------------------------------------------------------------
# Job `helper-release`: Entwurf des GitHub-Releases mit der Compose-Datei, die den Helfer per Digest nennt
# ---------------------------------------------------------------------------

DIGEST = "sha256:" + "ab" * 32
COMPOSE = "compose.standalone-mit-helfer.yml"
GH_STEP = "Entwurf anlegen oder Release ergänzen"


def _release_job() -> dict:
    return _workflow()["jobs"]["helper-release"]


def _release_named(name: str) -> dict:
    found = [s for s in _release_job()["steps"] if s.get("name") == name]
    assert len(found) == 1, f"genau ein Schritt {name!r} erwartet"
    return found[0]


def test_helper_digest_is_an_output_of_the_helper_job():
    assert _workflow()["jobs"]["updater-image"]["outputs"] == {"digest": "${{ steps.build.outputs.digest }}"}


def test_release_comes_after_tag_1_only_for_final_versions_with_least_privilege():
    job = _release_job()
    assert set(job) == {"needs", "if", "runs-on", "permissions", "steps"}, sorted(job)
    assert job["needs"] == "updater-image"
    assert job["if"] == "${{ !contains(github.ref_name, '-') }}"
    assert job["permissions"] == {"contents": "write"}
    checkout = job["steps"][0]
    assert str(checkout["uses"]).split("@")[0] == "actions/checkout"
    assert checkout["with"] == {"persist-credentials": False}, "kein Token mit Schreibrecht in der Arbeitskopie"
    assert [s for s in job["steps"] if "uses" in s] == [checkout], "keine fremde Action mit Schreibrecht"
    for step in job["steps"]:
        assert "continue-on-error" not in step and "if" not in step, step


def test_release_files_get_the_published_digest_only_through_the_environment():
    step = _release_named("Dateien fürs Release")
    assert step["env"] == {"IMAGE": "${{ env.IMAGE_NAME }}-updater", "DIGEST": "${{ needs.updater-image.outputs.digest }}"}
    assert "${{" not in step["run"]
    assert "\npython3 scripts/release_helper_assets.py files " in step["run"]


def test_only_the_gh_step_sees_the_token():
    step = _release_named(GH_STEP)
    assert step["env"] == {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}", "GH_REPO": "${{ github.repository }}", "TAG": "${{ github.ref_name }}"}
    assert "${{" not in step["run"]
    others = [s for s in _release_job()["steps"] if s.get("name") != step["name"]]
    assert all("GH_TOKEN" not in str(s) and "secrets" not in str(s) for s in others)


def _gh_flags(job: dict) -> dict[str, list[str]]:
    import re
    import shlex

    found: dict[str, list[str]] = {}
    for step in job["steps"]:
        for command, rest in re.findall(r"\bgh release (\w+) ([^\n;|&]*)", step.get("run", "")):
            assert command not in found, command
            found[command] = [token for token in shlex.split(rest) if token.startswith("-")]
    return found


def test_the_release_job_never_publishes_and_never_sets_latest():
    import re

    job = _release_job()
    text = json.dumps(job, ensure_ascii=False)
    assert re.findall(r"draft\W{0,4}(?:false|0|f)\b|--latest|make_latest|\bgh api\b", text, re.IGNORECASE) == []
    assert _gh_flags(job) == {
        "view": ["--json", "--jq"],
        "upload": ["--clobber"],
        "edit": ["--notes-file"],
        "create": ["--draft", "--verify-tag", "--title", "--notes-file"],
    }


# Ersatz für `gh` mit einem Release als Zustand, nach den Regeln von gh: `create` ohne `--draft` veröffentlicht, `edit`
# ändert nur, was als Schalter dasteht, `view`, `upload` und `edit` finden auch Entwürfe.
FAKE_GH = '''\
import json
import sys
from pathlib import Path

state, args = Path(sys.argv[1]), sys.argv[2:]
with (state / "calls.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")
switches, values = {"--draft", "--latest", "--prerelease", "--verify-tag", "--clobber"}, {"--title", "--notes-file", "--json", "--jq"}
flags, positional = {}, []
rest = iter(args[2:])
for arg in rest:
    name, eq, value = arg.partition("=")
    if name in switches:
        # Wie strconv.ParseBool in gh: alles andere ist ein Fehler.
        if not eq or value.lower() in ("1", "t", "true"):
            flags[name] = True
        elif value.lower() in ("0", "f", "false"):
            flags[name] = False
        else:
            sys.exit(f"invalid argument {value!r} for {name}")
    elif name in values:
        flags[name] = value if eq else next(rest)
    elif arg.startswith("-"):
        sys.exit(f"unknown flag: {arg}")
    else:
        positional.append(arg)
saved = state / "release.json"
release = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else None
command = args[1] if args[:1] == ["release"] else None
if command == "view" and (state / "view-error").exists():
    sys.exit((state / "view-error").read_text(encoding="utf-8"))
if command == "create":
    if release is not None:
        sys.exit("a release with the same tag name already exists")
    release = {"tag": positional[0], "draft": flags.get("--draft", False), "latest": flags.get("--latest"),
               "prerelease": flags.get("--prerelease", False), "title": flags.get("--title", ""), "body": "", "assets": {}}
elif command not in ("view", "upload", "edit"):
    sys.exit(f"not faked: {args}")
elif release is None or release["tag"] != positional[0]:
    sys.exit("release not found")
elif command == "view":
    sys.stdout.write(release["body"] + "\\n")
    sys.exit(0)
elif command == "edit":
    for flag, key in (("--draft", "draft"), ("--latest", "latest"), ("--prerelease", "prerelease"), ("--title", "title")):
        if flag in flags:
            release[key] = flags[flag]
if "--notes-file" in flags:
    release["body"] = Path(flags["--notes-file"]).read_bytes().decode("utf-8")
for path in positional[1:]:
    if command == "upload" and Path(path).name in release["assets"] and not flags.get("--clobber"):
        sys.exit("asset under the same name already exists")
    release["assets"][Path(path).name] = Path(path).read_text(encoding="utf-8")
saved.write_text(json.dumps(release), encoding="utf-8")
'''


def _fake_gh(tmp_path: Path, release: dict | None, view_error: str | None) -> tuple[Path, Path]:
    import sys

    state = tmp_path / "github"
    state.mkdir()
    if release is not None:
        (state / "release.json").write_text(json.dumps(release), encoding="utf-8")
    if view_error is not None:
        (state / "view-error").write_text(view_error, encoding="utf-8")
    (tmp_path / "fake_gh.py").write_text(FAKE_GH, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{tmp_path / "fake_gh.py"}" "{state}" "$@"\n', encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    return bin_dir, state


def _run_release(tmp_path: Path, release: dict | None = None, digest: str = DIGEST, *, version: str | None = None,
                 view_error: str | None = None, files_out: list | None = None):
    """Beide Schritte in bash wie auf dem Runner; zurück kommen Ergebnis, `gh`-Aufrufe, Release danach und RUNNER_TEMP."""
    import os
    import shutil
    import subprocess

    from nodvard_deck.version import __version__

    bash = shutil.which("bash")
    if bash is None or shutil.which("python3") is None:
        pytest.skip("braucht bash und python3 (wie der Runner von GitHub)")
    version = version or __version__
    bin_dir, state = _fake_gh(tmp_path, release and {"tag": f"v{version}", **release}, view_error)
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    image = _workflow()["env"]["IMAGE_NAME"] + "-updater"
    env = {**os.environ, "VERSION": version, "IMAGE": image, "DIGEST": digest, "RUNNER_TEMP": str(runner_temp)}
    files = subprocess.run([bash, "-eo", "pipefail", "-c", _release_named("Dateien fürs Release")["run"]], cwd=ROOT, env=env,
                           capture_output=True, text=True, check=False)
    if files_out is not None:
        files_out.append(files)
    done = files
    if files.returncode == 0:
        env.update(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", GH_TOKEN="x", GH_REPO="nodvard/deck", TAG=f"v{version}")
        done = subprocess.run([bash, "-eo", "pipefail", "-c", _release_named(GH_STEP)["run"]], cwd=ROOT, env=env,
                              capture_output=True, text=True, check=False)
    log, saved = state / "calls.jsonl", state / "release.json"
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    after = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else None
    return done, calls, after, runner_temp


def _old_section() -> str:
    return "<!-- nodvard-helper-digest -->\n## Update-Helfer / Update helper\n\nsha256:" + "cd" * 32 + "\n<!-- /nodvard-helper-digest -->"


def test_a_new_release_is_only_a_draft_with_the_text_the_pinned_file_and_the_digest(tmp_path):
    done, calls, release, runner_temp = _run_release(tmp_path, version="0.11.0")
    assert done.returncode == 0, done.stderr
    assert [c[:2] for c in calls] == [["release", "view"], ["release", "create"]]
    out = runner_temp / "release"
    image = _workflow()["env"]["IMAGE_NAME"] + "-updater"
    pinned = (out / COMPOSE).read_text(encoding="utf-8")
    assert f"image: {image}@{DIGEST}" in pinned and f"image: {image}:1" not in pinned
    assert release == {
        "tag": "v0.11.0", "draft": True, "latest": None, "prerelease": False,
        "title": (out / "release-title.txt").read_text(encoding="utf-8").strip(),
        "body": (out / "release-notes.md").read_text(encoding="utf-8"),
        "assets": {COMPOSE: pinned},
    }
    assert release["title"].startswith("Nodvard Deck 0.11.0 – ")
    assert release["body"].startswith("*English version: [below](#english).*\n")
    assert f"`{image}@{DIGEST}`" in release["body"]
    assert "::notice::Entwurf für v0.11.0 angelegt." in done.stdout


def test_an_existing_draft_keeps_title_status_and_text_around_the_section(tmp_path):
    body = f"Von Hand geschrieben.\r\n\r\n{_old_section()}\r\n\r\nDarunter auch.\r\n"
    draft = {"draft": True, "latest": None, "prerelease": False, "title": "Von Hand", "body": body, "assets": {}}
    done, calls, release, runner_temp = _run_release(tmp_path, draft, version="0.11.0")
    assert done.returncode == 0, done.stderr
    assert [c[:2] for c in calls] == [["release", "view"], ["release", "upload"], ["release", "edit"]]
    out = runner_temp / "release"
    section = (out / "helper-digest.md").read_text(encoding="utf-8").rstrip("\n")
    assert release == {
        **draft, "tag": "v0.11.0",
        "body": f"Von Hand geschrieben.\r\n\r\n{section}\r\n\r\nDarunter auch.\r\n",
        "assets": {COMPOSE: (out / COMPOSE).read_text(encoding="utf-8")},
    }
    assert "::notice::v0.11.0 ergänzt" in done.stdout


def test_a_published_release_stays_published_and_gets_the_new_file(tmp_path):
    published = {"draft": False, "latest": True, "prerelease": False, "title": "Schon draußen", "body": "Von Hand.",
                 "assets": {COMPOSE: "alt\n"}}
    done, _, release, runner_temp = _run_release(tmp_path, published)
    assert done.returncode == 0, done.stderr
    out = runner_temp / "release"
    assert release == {
        **published, "tag": release["tag"],
        "body": "Von Hand.\n\n" + (out / "helper-digest.md").read_text(encoding="utf-8"),
        "assets": {COMPOSE: (out / COMPOSE).read_text(encoding="utf-8")},
    }


@pytest.mark.parametrize("body", [
    "Oben.\n\n<!-- nodvard-helper-digest -->\nalt\n\nVon Hand darunter.\n",
    f"Oben.\n\n{_old_section()}\n\n{_old_section()}\n",
], ids=["ohne-ende", "doppelt"])
def test_a_broken_section_in_a_draft_changes_nothing(tmp_path, body):
    draft = {"draft": True, "latest": None, "prerelease": False, "title": "Von Hand", "body": body, "assets": {}}
    done, calls, release, _ = _run_release(tmp_path, draft)
    assert done.returncode == 1 and "nicht genau einmal vollständig" in done.stderr
    assert [c[:2] for c in calls] == [["release", "view"]]
    assert release == {**draft, "tag": release["tag"]}


def test_another_error_when_looking_up_the_release_stops_the_job(tmp_path):
    done, calls, release, _ = _run_release(tmp_path, view_error="HTTP 502: Bad Gateway")
    assert done.returncode == 1 and "HTTP 502" in done.stderr
    assert [c[:2] for c in calls] == [["release", "view"]] and release is None


@pytest.mark.parametrize(("version", "warned"), [("0.11.0", False), ("99.0.0", True)])
def test_a_missing_release_text_gives_a_warning(tmp_path, version, warned):
    assert (ROOT / "docs" / "release-notes" / "0.11.0.md").is_file()
    files: list = []
    done, _, _, _ = _run_release(tmp_path, version=version, files_out=files)
    assert done.returncode == 0, done.stderr
    assert ("::warning::docs/release-notes/99.0.0.md fehlt" in files[0].stdout) == warned


def test_a_bad_digest_stops_before_anything_reaches_github(tmp_path):
    done, calls, release, runner_temp = _run_release(tmp_path, digest="")
    assert done.returncode == 1 and "Kein Digest" in done.stderr
    assert calls == [] and release is None and not (runner_temp / "release").exists()


# ---------------------------------------------------------------------------
# Job `dock-image`: Nodvard Dock, das Dashboard nur mit dem Modul docker
# ---------------------------------------------------------------------------

DOCK_COMPOSE = ROOT / "deploy" / "nodvard-dock" / "compose.yml"


def _dock_job() -> dict:
    return _workflow()["jobs"]["dock-image"]


def _dock_step(uses_prefix: str) -> tuple[int, dict]:
    found = [(i, s) for i, s in enumerate(_dock_job()["steps"]) if str(s.get("uses", "")).split("@")[0] == uses_prefix]
    assert len(found) == 1, f"genau ein Schritt mit {uses_prefix} erwartet"
    return found[0]


def _dock_named(name: str) -> tuple[int, dict]:
    found = [(i, s) for i, s in enumerate(_dock_job()["steps"]) if s.get("name") == name]
    assert len(found) == 1, f"genau ein Schritt {name!r} erwartet"
    return found[0]


def test_dock_image_name_sits_next_to_the_dashboard_name():
    yaml = pytest.importorskip("yaml")
    env = _workflow()["env"]
    assert env["DOCK_IMAGE_NAME"] == "ghcr.io/nodvard/dock"
    assert env["DOCK_IMAGE_NAME"].rsplit("/", 1)[0] == env["IMAGE_NAME"].rsplit("/", 1)[0]
    assert RELEASE.read_text(encoding="utf-8").count("ghcr.io/nodvard/dock") == 1
    _, meta = _dock_step("docker/metadata-action")
    assert meta["with"]["images"] == "${{ env.DOCK_IMAGE_NAME }}"
    compose = yaml.safe_load(DOCK_COMPOSE.read_text(encoding="utf-8"))
    assert compose["services"]["nodvard-dock"]["image"] == env["DOCK_IMAGE_NAME"] + ":latest"


def test_dock_image_waits_for_the_dashboard_image():
    # Ein Tag, das "Tag passt zu version.py" ablehnt, bringt so auch kein Dock-Image.
    job = _dock_job()
    assert job["needs"] == "image"
    assert set(job) == {"needs", "runs-on", "permissions", "steps"}, sorted(job)


def test_dock_image_is_the_dashboard_dockerfile_with_only_the_docker_module():
    _dock_step("docker/setup-qemu-action")
    _dock_step("docker/setup-buildx-action")
    _, build = _dock_step("docker/build-push-action")
    args = build["with"]
    assert build["id"] == "build"
    assert args["file"] == "deploy/Dockerfile"
    assert args["context"] == "."
    assert args["platforms"] == "linux/amd64,linux/arm64"
    assert args["push"] is True
    assert args["tags"] == "${{ steps.meta.outputs.tags }}"
    assert args["labels"] == "${{ steps.meta.outputs.labels }}"
    assert dict(line.split("=", 1) for line in args["build-args"].strip().splitlines()) == {
        "VERSION": "${{ env.VERSION }}",
        "IMAGE": "${{ env.DOCK_IMAGE_NAME }}",
        "NODVARD_DECK_EXTENSIONS": "docker",
    }
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "--build-arg NODVARD_DECK_EXTENSIONS=docker -t ghcr.io/nodvard/dock:latest" in ci


def test_dock_image_reads_the_dashboard_cache_but_writes_its_own():
    _, build = _dock_step("docker/build-push-action")
    dashboard = _step("docker/build-push-action")["with"]
    assert build["with"]["cache-from"].split() == [dashboard["cache-from"], "type=gha,scope=dock"]
    assert build["with"]["cache-to"] == "type=gha,scope=dock,mode=max"
    assert "scope=" not in dashboard["cache-to"]


def test_dock_tags_and_labels():
    _, meta = _dock_step("docker/metadata-action")
    args = meta["with"]
    assert "latest=false" in args["flavor"]
    assert [line.strip() for line in args["tags"].strip().splitlines()] == ["type=semver,pattern={{version}}"]
    labels = args["labels"]
    for label in ("org.opencontainers.image.title=Nodvard Dock", "org.opencontainers.image.source=",
                  "org.opencontainers.image.version=${{ env.VERSION }}",
                  "org.opencontainers.image.licenses=PolyForm-Noncommercial-1.0.0"):
        assert label in labels, label


def test_dock_version_login_and_actions_as_for_the_dashboard():
    _, step = _dock_named("Version aus dem Tag")
    _, dashboard = _named("Version aus dem Tag")
    assert step == dashboard
    _, login = _dock_step("docker/login-action")
    assert login["with"] == {"registry": "ghcr.io", "username": "${{ github.actor }}", "password": "${{ secrets.GITHUB_TOKEN }}"}
    pinned = {str(s["uses"]).split("@")[0]: s["uses"] for s in _steps() if "uses" in s}
    for step in _dock_job()["steps"]:
        if "uses" in step:
            assert step["uses"] == pinned[str(step["uses"]).split("@")[0]], step["uses"]


def test_dock_latest_moves_only_after_the_check_and_only_for_the_newest_version():
    steps = _dock_job()["steps"]
    build_index, _ = _dock_step("docker/build-push-action")
    check_index, check = _dock_named("Prüfung am Digest (amd64 und arm64)")
    latest_index, latest = _dock_named("Tag latest setzen")
    assert build_index < check_index == latest_index - 1 == len(steps) - 2
    environment = {"IMAGE": "${{ env.DOCK_IMAGE_NAME }}", "DIGEST": "${{ steps.build.outputs.digest }}"}
    assert set(check) == {"name", "env", "run"}, sorted(check)
    assert check["env"] == environment
    assert "${{" not in check["run"], "Image und Digest nur über die Umgebung, nie in den Befehl eingesetzt"
    assert "||" not in check["run"] and "set +e" not in check["run"]
    for step in steps:
        assert "continue-on-error" not in step, step
        if "if" in step:
            assert step.get("name") == "Tag latest setzen", step
    assert latest["if"] == "${{ needs.image.outputs.newest == 'true' }}"
    assert latest["env"] == environment
    assert latest["run"].strip() == 'docker buildx imagetools create --tag "$IMAGE:latest" "$IMAGE@$DIGEST"'


def _run_dock_check(tmp_path: Path, modules: list[str], *, tesseract: bool = False, fail: bool = False):
    """Führt die Prüfung mit einem nachgebauten `docker` aus, das den Befehl im Container hier auf einen Ordner lenkt."""
    import os
    import shutil
    import subprocess
    import sys

    if sys.platform == "win32":
        pytest.skip("braucht POSIX-Pfade und Symlinks (wie der Runner von GitHub)")
    bash, sh = shutil.which("bash"), shutil.which("sh")
    tools = {name: shutil.which(name) for name in ("ls", "tr", "sed")}
    if bash is None or sh is None or None in tools.values():
        pytest.skip("braucht bash, sh, ls, tr und sed (wie der Runner von GitHub)")
    for module in modules:
        (tmp_path / "app" / "extensions" / module).mkdir(parents=True)
    image_bin = tmp_path / "image-bin"
    image_bin.mkdir()
    for name in ("ls", "tr"):
        (image_bin / name).symlink_to(tools[name])
    if tesseract:
        (image_bin / "tesseract").write_text("#!/bin/sh\n", encoding="utf-8")
        (image_bin / "tesseract").chmod(0o755)
    host_bin = tmp_path / "host-bin"
    host_bin.mkdir()
    (host_bin / "docker").write_text(
        "#!/bin/sh\n"
        'echo "$*" >> "$FAKE_ROOT/calls"\n'
        'if [ "$1" = run ]; then\n'
        '  [ -n "$FAKE_FAIL" ] && exit 125\n'
        '  while [ "$1" != -c ]; do shift; done\n'
        f'  script="$(printf \'%s\' "$2" | {tools["sed"]} "s#/app/extensions#$FAKE_ROOT/app/extensions#")"\n'
        f'  exec env -i LC_ALL=C PATH="$FAKE_ROOT/image-bin" {sh} -c "$script"\n'
        "fi\n",
        encoding="utf-8",
    )
    (host_bin / "docker").chmod(0o755)
    _, check = _dock_named("Prüfung am Digest (amd64 und arm64)")
    env = {**os.environ, "PATH": f"{host_bin}{os.pathsep}{os.environ.get('PATH', '')}", "FAKE_ROOT": str(tmp_path),
           "IMAGE": "ghcr.io/nodvard/dock", "DIGEST": "sha256:" + "a" * 64, "FAKE_FAIL": "1" if fail else ""}
    done = subprocess.run([bash, "-eo", "pipefail", "-c", check["run"]], cwd=tmp_path, env=env, capture_output=True,
                          text=True, check=False)
    calls = (tmp_path / "calls").read_text(encoding="utf-8").splitlines() if (tmp_path / "calls").exists() else []
    return done, calls


def test_dock_check_passes_with_only_the_docker_module_on_both_platforms(tmp_path):
    done, calls = _run_dock_check(tmp_path, ["_shared", "docker"])
    assert done.returncode == 0, done.stderr
    runs = [call for call in calls if call.startswith("run ")]
    assert [call.split("--platform ")[1].split()[0] for call in runs] == ["linux/amd64", "linux/arm64"]
    assert all("--network none" in call and "ghcr.io/nodvard/dock@sha256:" in call for call in runs)
    assert len([call for call in calls if call.startswith("image rm ")]) == 2


@pytest.mark.parametrize(
    ("modules", "tesseract", "fail"),
    [
        (["_shared", "docker", "documents"], False, False),
        (["_shared"], False, False),
        ([], False, False),
        (["_shared", "docker"], True, False),
        (["_shared", "docker"], False, True),
    ],
    ids=["weiteres-modul", "ohne-docker", "ohne-ordner", "tesseract", "start-scheitert"],
)
def test_dock_check_stops_before_latest(tmp_path, modules, tesseract, fail):
    done, calls = _run_dock_check(tmp_path, modules, tesseract=tesseract, fail=fail)
    assert done.returncode != 0
    assert not any(call.startswith("image rm ") for call in calls)
    if not fail:
        assert "Erwartet war nur das Modul docker" in done.stderr
