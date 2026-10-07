"""`.github/workflows/release.yml`: Image bei jedem Tag `v*` bauen und nach ghcr.io schieben.

Kein Deploy, keine eigenen Geheimnisse: nur der automatisch vergebene `GITHUB_TOKEN`. Der Image-Name
steht ganz oben als Variable, weil die Organisation "nodvard" erst angelegt werden muss. Der zweite Job
`updater-image` baut danach das Image des Update-Helfers (deploy/updater) unter demselben Namen mit "-updater".
Die Tests lesen nur die YAML-Struktur (kein Lauf bei GitHub).
"""

from __future__ import annotations

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
    # `latest` nie automatisch und nie fuer Vorabversionen (v1.2.3-rc1).
    assert "latest=false" in meta["flavor"]
    assert "contains(github.ref_name, '-')" in tags


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
    assert args == {"VERSION": "${{ env.VERSION }}", "IMAGE": "${{ env.IMAGE_NAME }}"}


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


def test_jobs_are_the_dashboard_image_and_the_helper_image():
    assert list(_workflow()["jobs"]) == ["image", "updater-image"]


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
    assert set(job) == {"needs", "runs-on", "permissions", "steps"}, sorted(job)
    _, selftest = _updater_named("Selbsttest am Digest (amd64 und arm64)")
    assert set(selftest) == {"name", "env", "run"}, sorted(selftest)
    assert "||" not in selftest["run"] and "set +e" not in selftest["run"]
    for step in _updater_steps():
        assert "continue-on-error" not in step, step
        if "if" in step:
            assert step.get("name") == "Tag 1 setzen", step


def test_helper_tag_1_moves_only_after_the_selftest_and_never_for_a_prerelease():
    """`:1` nimmt die Compose-Datei mit Helfer. Er wandert erst, wenn der Selbsttest bestanden ist, und nie fuer eine
    Vorabversion (wie `latest` beim Dashboard-Image)."""
    test_index, _ = _updater_named("Selbsttest am Digest (amd64 und arm64)")
    tag_index, step = _updater_named("Tag 1 setzen")
    assert tag_index == test_index + 1 == len(_updater_steps()) - 1
    assert step["if"] == "${{ !contains(github.ref_name, '-') }}"
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
